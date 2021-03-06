# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import argparse
import numpy as np
import tensorflow as tf

NUM_CLASSES = 10


def model_fn(features, labels, mode, params):
    # build model
    global_step = tf.train.get_global_step()

    conv = tf.layers.conv2d(features, filters=16, kernel_size=(4, 4), strides=(2, 2))
    max_pool = tf.layers.max_pooling2d(conv, pool_size=(4, 4), strides=(2, 2))

    conv_1 = tf.layers.conv2d(max_pool, filters=32, kernel_size=(1, 1), strides=(1, 1))
    max_pool_1 = tf.layers.max_pooling2d(conv_1, pool_size=(2, 2), strides=(2, 2))

    batch_size = features.shape[0]
    flattened = tf.reshape(max_pool_1, (batch_size, -1))
    logits = tf.layers.dense(flattened, NUM_CLASSES)

    predictions = tf.multinomial(logits, num_samples=1)
    loss = None
    train_op = None

    if mode == tf.estimator.ModeKeys.TRAIN:
        # define loss
        loss = tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(labels=labels, logits=logits))

        # define train_op
        optimizer = tf.train.RMSPropOptimizer(learning_rate=0.05)

        # wrapper to make the optimizer work with TPUs
        if params['use_tpu']:
            optimizer = tf.contrib.tpu.CrossShardOptimizer(optimizer)

        train_op = optimizer.minimize(loss, global_step=global_step)

    if params['use_tpu']:
        # TPU version of EstimatorSpec
        return tf.contrib.tpu.TPUEstimatorSpec(
            mode=mode,
            predictions=predictions,
            loss=loss,
            train_op=train_op
        )
    else:
        return tf.estimator.EstimatorSpec(
            mode=mode,
            predictions=predictions,
            loss=loss,
            train_op=train_op
        )


def train_input_fn(params={}):
    # make some fake image classification data
    data_size = 100
    x = np.random.rand(data_size, 28, 28, 1)
    y = np.random.randint(0, NUM_CLASSES, data_size)

    x_tensor = tf.constant(x, dtype=tf.float32)
    y_tensor = tf.constant(y, dtype=tf.int32)
    dataset = tf.data.Dataset.from_tensor_slices((x_tensor, y_tensor))
    dataset = dataset.repeat()

    # TPUEstimator passes params when calling input_fn
    batch_size = params.get('train_batch_size', 16)
    dataset = dataset.batch(batch_size, drop_remainder=True)

    # TPUs need to know all dimensions when the graph is built
    # Datasets know the batch size only when the graph is run
    def set_shapes(features, labels):
        features_shape = features.get_shape().merge_with([batch_size, None, None, None])
        labels_shape = labels.get_shape().merge_with([batch_size,])

        features.set_shape(features_shape)
        labels.set_shape(labels_shape)

        return features, labels

    dataset = dataset.map(set_shapes)
    dataset = dataset.prefetch(tf.contrib.data.AUTOTUNE)

    return dataset


def main(args):
    # pass the args as params so the model_fn can use
    # the TPU specific args
    params = vars(args)

    if args.use_tpu:
        # additional configs required for using TPUs
        tpu_cluster_resolver = tf.contrib.cluster_resolver.TPUClusterResolver(args.tpu)
        tpu_config = tf.contrib.tpu.TPUConfig(
            num_shards=8, # using Cloud TPU v2-8
            iterations_per_loop=args.save_checkpoints_steps
        )

        # use the TPU version of RunConfig
        config = tf.contrib.tpu.RunConfig(
            cluster=tpu_cluster_resolver,
            model_dir=args.model_dir,
            tpu_config=tpu_config,
            save_checkpoints_steps=args.save_checkpoints_steps,
            save_summary_steps=100
        )

        # TPUEstimator
        estimator = tf.contrib.tpu.TPUEstimator(
            model_fn=model_fn,
            config=config,
            params=params,
            train_batch_size=args.train_batch_size,
            eval_batch_size=32, # FIXME
            export_to_tpu=False
        )
    else:
        config = tf.estimator.RunConfig(model_dir=args.model_dir)

        estimator = tf.estimator.Estimator(
            model_fn,
            config=config,
            params=params
        )

    estimator.train(train_input_fn, max_steps=args.max_steps)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--model-dir',
        type=str,
        default='/tmp/tpu-template'
    )
    parser.add_argument(
        '--max-steps',
        type=int,
        default=1000
    )
    parser.add_argument(
        '--train-batch-size',
        type=int,
        default=16
    )
    parser.add_argument(
        '--save-checkpoints-steps',
        type=int,
        default=100
    )
    parser.add_argument(
        '--use-tpu',
        action='store_true'
    )
    parser.add_argument(
        '--tpu',
        default=None
    )

    args, _ = parser.parse_known_args()

    # colab.research.google.com specific
    import sys
    if 'google.colab' in sys.modules:
        import json
        import os
        from google.colab import auth

        # Authenticate to access GCS bucket
        auth.authenticate_user()

        # TODO(user): change this
        args.model_dir = 'gs://your-gcs-bucket'

        # When connected to the TPU runtime
        if 'COLAB_TPU_ADDR' in os.environ:
            tpu_grpc = 'grpc://{}'.format(os.environ['COLAB_TPU_ADDR'])

            args.tpu = tpu_grpc
            args.use_tpu = True

            # Upload credentials to the TPU
            with tf.Session(tpu_grpc) as sess:
                data = json.load(open('/content/adc.json'))
                tf.contrib.cloud.configure_gcs(sess, credentials=data)

    main(args)
