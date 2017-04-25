#
# Copyright 2016, 2017 Chris Cummins <chrisc.101@gmail.com>.
#
# This file is part of CLgen.
#
# CLgen is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# CLgen is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with CLgen.  If not, see <http://www.gnu.org/licenses/>.
#
"""
CLgen model.
"""
import os
import re
import sys
import tarfile

from copy import deepcopy
from time import time

import progressbar

from labm8 import crypto
from labm8 import fs
from labm8 import jsonutil
from labm8 import lockfile
from labm8 import system
from labm8 import types

import clgen
from clgen import log


def get_default_author() -> str:
    """
    Get the default author name for CLgen dist models.

    If CLGEN_AUTHOR environment variable is set, use that. Else, author
    is $USER@$HOSTNAME.

    Returns:
        str: Author name.
    """
    return os.environ.get(
        "CLGEN_AUTHOR",
        "{user}@{host}".format(user=system.USERNAME, host=system.HOSTNAME))


# Default options used for model. Any values provided by the user will override
# these defaults.
DEFAULT_MODEL_OPTS = {
    "author": get_default_author(),
    "architecture": {
        "model_type": "lstm",  # {lstm,rnn.gru}
        "rnn_size": 128,  # num nodes in layer
        "num_layers": 2,  # num layers
    },
    "train_opts": {
        "epochs": 10,
        "grad_clip": 5,
        "learning_rate": 2e-3,  # initial learning rate
        "lr_decay_rate": 5,  # % to reduce learning rate by per epoch
        "intermediate_checkpoints": True
    }
}


class ModelError(clgen.CLgenError):
    """
    Module level error
    """
    pass


class Model(clgen.CLgenObject):
    """
    A CLgen Model.
    """
    def __init__(self, corpus: clgen.Corpus, **opts):
        """
        Instantiate model.

        Arguments:
            corpus (clgen.Corpus): Corpus instance.
            opts (dict): Training options.
        """
        def _hash(corpus: clgen.Corpus, opts: dict) -> str:
            """ compute model hash """
            hashopts = deepcopy(opts)
            del hashopts["train_opts"]["epochs"]
            return crypto.sha1_list(corpus.hash, *types.dict_values(hashopts))

        assert(isinstance(corpus, clgen.Corpus))

        # Validate options
        for key in opts:
            if key not in DEFAULT_MODEL_OPTS:
                raise clgen.UserError(
                    "Unsupported model option '{}'. Valid keys: {}".format(
                        key, ','.join(sorted(DEFAULT_MODEL_OPTS.keys()))))

        # set properties
        self.opts = types.update(deepcopy(DEFAULT_MODEL_OPTS), opts)
        self.corpus = corpus
        self.hash = _hash(self.corpus, self.opts)
        self.cache = clgen.mkcache("model", self.hash)

        log.debug("model", self.hash)

        # validate metadata against cache, and restore stats
        self.stats = {
            "epoch_times": [],
            "epoch_costs": [],
            "epoch_batches": []
        }
        meta = deepcopy(self.to_json())
        if self.cache.get("META"):
            cached_meta = jsonutil.read_file(self.cache["META"])
            self.stats = cached_meta["stats"]  # restore stats

            del cached_meta["stats"]
            del meta["stats"]

            del cached_meta["train_opts"]["epochs"]
            del meta["train_opts"]["epochs"]

            if meta != cached_meta:
                raise clgen.InternalError("model metadata mismatch")
        else:
            self._flush_meta()


    def _init_tensorflow(self, infer: bool=False):
        """
        Deferred importing of tensorflow and initializing model for training
        or sampling.

        This is necessary for two reasons: first, the tensorflow graph is
        different for training and inference, so must be reset when switching
        between modes. Second, importing tensorflow takes a long time, so
        we only want to do it if we actually need to.

        Arguments:
            infer (bool): If True, initialize model for inference. If False,
                initialize model for training.

        Returns:
            module: imported TensorFlow module
        """
        # quiet tensorflow. See: https://github.com/tensorflow/tensorflow/issues/1258
        os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

        import tensorflow as tf
        import tensorflow.contrib.legacy_seq2seq as seq2seq
        from tensorflow.contrib import rnn

        self.cell_fn = {
            "lstm": rnn.BasicLSTMCell,
            "gru": rnn.GRUCell,
            "rnn": rnn.BasicRNNCell
        }.get(self.model_type, None)
        if self.cell_fn is None:
            raise clgen.UserError("Unrecognized model type")

        # reset the graph when switching between training and inference
        tf.reset_default_graph()

        # corpus info:
        batch_size = 1 if infer else self.corpus.batch_size
        seq_length = 1 if infer else self.corpus.seq_length
        vocab_size = self.corpus.vocab_size

        cell = self.cell_fn(self.rnn_size, state_is_tuple=True)
        self.cell = cell = rnn.MultiRNNCell(
            [cell] * self.num_layers, state_is_tuple=True)
        self.input_data = tf.placeholder(tf.int32, [batch_size, seq_length])
        self.targets = tf.placeholder(tf.int32, [batch_size, seq_length])
        self.initial_state = self.cell.zero_state(batch_size, tf.float32)

        scope_name = 'rnnlm'
        with tf.variable_scope(scope_name):
            softmax_w = tf.get_variable("softmax_w",
                                        [self.rnn_size, vocab_size])
            softmax_b = tf.get_variable("softmax_b", [vocab_size])

            with tf.device("/cpu:0"):
                embedding = tf.get_variable("embedding",
                                            [vocab_size, self.rnn_size])
                inputs = tf.split(
                    axis=1, num_or_size_splits=seq_length,
                    value=tf.nn.embedding_lookup(embedding, self.input_data))
                inputs = [tf.squeeze(input_, [1]) for input_ in inputs]

        def loop(prev, _):
            prev = tf.matmul(prev, softmax_w) + softmax_b
            prev_symbol = tf.stop_gradient(tf.argmax(prev, 1))
            return tf.nn.embedding_lookup(embedding, prev_symbol)

        outputs, last_state = seq2seq.rnn_decoder(
            inputs, self.initial_state, cell,
            loop_function=loop if infer else None, scope=scope_name)
        output = tf.reshape(tf.concat(axis=1, values=outputs), [-1, self.rnn_size])
        self.logits = tf.matmul(output, softmax_w) + softmax_b
        self.probs = tf.nn.softmax(self.logits)
        loss = seq2seq.sequence_loss_by_example(
            [self.logits],
            [tf.reshape(self.targets, [-1])],
            [tf.ones([batch_size * seq_length])],
            vocab_size)
        self.cost = tf.reduce_sum(loss) / batch_size / seq_length
        self.final_state = last_state
        self.learning_rate = tf.Variable(0.0, trainable=False)
        self.epoch = tf.Variable(0, trainable=False)
        tvars = tf.trainable_variables()
        grads, _ = tf.clip_by_global_norm(
            tf.gradients(self.cost, tvars), self.grad_clip)
        optimizer = tf.train.AdamOptimizer(self.learning_rate)
        self.train_op = optimizer.apply_gradients(zip(grads, tvars))

        return tf


    def _get_params_path(self, ckpt) -> str:
        """ return path to checkpoint closest to target num of epochs """
        paths = ckpt.all_model_checkpoint_paths
        batch_nums = [int(x.split('-')[-1]) for x in paths]
        epoch_nums = [int((x + 1) / (self.corpus.num_batches))
                      for x in batch_nums]

        closest = self.epochs
        closest_path = None
        for e, path in zip(epoch_nums, paths):
            diff = self.epochs - e
            if diff >= 0 and diff < closest:
                log.verbose("  cached checkpoint at epoch =", e, "diff =", diff)
                closest = diff
                closest_path = path

        return closest_path, paths


    def _locked_train(self):
        tf = self._init_tensorflow(infer=False)

        # training options
        learning_rate = self.train_opts["learning_rate"]
        decay_rate = self.train_opts["lr_decay_rate"]

        # resume from prior checkpoint
        ckpt_path, ckpt_paths = None, None
        if self.checkpoint_path:
            # check that all necessary files exist
            assert(fs.isdir(self.checkpoint_path))
            ckpt = tf.train.get_checkpoint_state(self.checkpoint_path)
            assert(ckpt)
            assert(ckpt.model_checkpoint_path)
            ckpt_path, ckpt_paths = self._get_params_path(ckpt)

        with tf.Session() as sess:
            tf.global_variables_initializer().run()

            # keep all checkpoints
            saver = tf.train.Saver(tf.global_variables(), max_to_keep=100)

            # restore model from closest checkpoint
            if ckpt_path:
                log.debug("restoring", ckpt_path)
                saver.restore(sess, ckpt_path)
                log.verbose("restored checkpoint {}".format(ckpt_path))

            # make sure we don't lose track of other checkpoints
            if ckpt_paths:
                saver.recover_last_checkpoints(ckpt_paths)

            max_batch = self.epochs * self.corpus.num_batches

            # progress bar
            bar = progressbar.ProgressBar(max_value=max_batch)

            if sess.run(self.epoch) != self.epochs:
                log.info("training", self)

            for e in range(sess.run(self.epoch) + 1, self.epochs + 1):
                epoch_start = time()

                # decay and set learning rate
                new_learning_rate = learning_rate * (
                    (float(100 - decay_rate) / 100.0) ** (e - 1))
                sess.run(tf.assign(self.learning_rate, new_learning_rate))
                sess.run(tf.assign(self.epoch, e))

                self.corpus.create_batches()

                state = sess.run(self.initial_state)
                for b in range(self.corpus.num_batches):
                    x, y = self.corpus.next_batch()
                    feed = {self.input_data: x, self.targets: y}
                    for i, (c, h) in enumerate(self.initial_state):
                        feed[c] = state[i].c
                        feed[h] = state[i].h
                    train_cost, state, _ = sess.run(
                        [self.cost, self.final_state, self.train_op], feed)

                    # update progress bar
                    batch_num = (e - 1) * self.corpus.num_batches + b
                    bar.update(batch_num)

                save = self.opts["train_opts"]["intermediate_checkpoints"]
                save |= e == self.epochs  # always save on last epoch
                if save:
                    saver.save(sess, self.cache.keypath("model.ckpt"),
                               global_step=batch_num)

                    next_checkpoint = e * self.corpus.num_batches + b
                    max_epoch = self.epochs
                    log.verbose("\n{self} epoch {e} / {max_epoch}. "
                                "next checkpoint at batch {next_checkpoint}"
                                .format(**vars()))

                    # update training time
                    epoch_duration = time() - epoch_start
                    self.stats["epoch_costs"].append(float(train_cost))
                    self.stats["epoch_times"].append(epoch_duration)
                    self.stats["epoch_batches"].append(batch_num + 1)
                    self._flush_meta()

        return self


    def _flush_meta(self):
        jsonutil.write_file(self.cache.keypath("META"), self.to_json())


    def train(self):
        """
        Train model.

        Returns:
            Model: self.
        """
        with self.lock.acquire():
            return self._locked_train()

    @property
    def lock(self):
        lockpath = self.cache.keypath("LOCK")
        return lockfile.LockFile(lockpath)

    @property
    def model_type(self) -> str:
        return self.opts["architecture"]["model_type"]

    @property
    def rnn_size(self) -> int:
        return self.opts["architecture"]["rnn_size"]

    @property
    def num_layers(self) -> int:
        return self.opts["architecture"]["num_layers"]

    @property
    def grad_clip(self) -> int:
        return self.train_opts["grad_clip"]

    @property
    def epochs(self) -> int:
        return self.train_opts["epochs"]

    @property
    def train_opts(self) -> dict:
        return self.opts["train_opts"]

    def __repr__(self) -> str:
        """
        String representation.
        """
        hash = self.hash
        size = self.rnn_size
        nlayers = self.num_layers
        model = self.model_type.upper()
        nepochs = self.epochs

        return "model[{hash}]: {size}x{nlayers}x{nepochs} {model}".format(**vars())

    def to_json(self) -> dict:
        d = deepcopy(self.opts)
        d["corpus"] = self.corpus.to_json()
        d["stats"] = self.stats
        return d

    def __eq__(self, rhs) -> bool:
        if not isinstance(rhs, Model):
            return False
        return rhs.hash == self.hash

    def __ne__(self, rhs) -> bool:
        return not self.__eq__(rhs)

    @property
    def checkpoint_path(self) -> str:
        """
        Get path to most recemt checkpoint, if exists.

        Returns:

            str or None: Path to checkpoint, or None if no checkpoints.
        """
        if self.cache.get("checkpoint"):
            return self.cache.path
        else:
            return None


    @staticmethod
    def from_json(model_json: dict) -> 'Model':
        """
        Load model from JSON.

        Arguments:
            model_json (dict): JSON specification.

        Returns:
            Model: Model instance.
        """
        assert(isinstance(model_json, dict))

        if "corpus" not in model_json:
            raise clgen.UserError("model JSON has no corpus entry")

        # create corpus and remove from JSON
        corpus = clgen.Corpus.from_json(model_json.pop("corpus"))

        if "stats" in model_json:  # ignore stats
            del model_json["stats"]

        return Model(corpus, **model_json)
