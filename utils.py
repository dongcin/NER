# -*- coding: utf-8 -*
import tensorflow as tf
from tensorflow.python.ops import lookup_ops
import numpy as np
import collections
import time

src_file = 'resource/source.txt'
tgt_file = 'resource/target.txt'
src_vocab_file = 'resource/source_vocab.txt'
tgt_vocab_file = 'resource/target_vocab.txt'
word_embedding_file = 'resource/wiki.zh.vec'
embeddings_size = 30
PADDING_ID = -1
UNK_ID = -2
'''
reverse_tgt_vocab_table = lookup_ops.index_to_string_table_from_file(
        tgt_vocab_file, default_value=vocab_utils.UNK)
inference.py 56:line
'''

class BatchedInput(collections.namedtuple("BatchedInput",
                                          ("initializer",
                                           "source",
                                           "target_input",
                                           "source_sequence_length",
                                           "target_sequence_length"))):
  pass


def get_src_vocab_size():
    size = 0
    with open(src_vocab_file, 'r') as vocab_file:
        for content in vocab_file.readlines():
            content = content.strip()
            if content != '':
                size += 1
    return size


def create_vocab_tables(src_vocab_file, tgt_vocab_file, src_unknown_id, tgt_unknown_id, share_vocab=False):
  src_vocab_table = lookup_ops.index_table_from_file(
      src_vocab_file, default_value=src_unknown_id)
  if share_vocab:
    tgt_vocab_table = src_vocab_table
  else:
    tgt_vocab_table = lookup_ops.index_table_from_file(
        tgt_vocab_file, default_value=tgt_unknown_id)
  return src_vocab_table, tgt_vocab_table


def get_iterator(src_vocab_table, tgt_vocab_table, batch_size, buffer_size=None, random_seed=None, num_threads=1,
                 src_max_len=100, tgt_max_len=100, num_buckets=5):
    if buffer_size is None:
        buffer_size = batch_size * 1000

    src_dataset = tf.contrib.data.TextLineDataset(src_file)
    tgt_dataset = tf.contrib.data.TextLineDataset(tgt_file)
    src_tgt_dataset = tf.contrib.data.Dataset.zip((src_dataset, tgt_dataset))

    src_tgt_dataset = src_tgt_dataset.shuffle(
        buffer_size, random_seed)

    src_tgt_dataset = src_tgt_dataset.map(
        lambda src, tgt: (
            tf.string_split([src]).values, tf.string_split([tgt]).values),
        num_threads=num_threads,
        output_buffer_size=buffer_size)

    src_tgt_dataset = src_tgt_dataset.filter(
        lambda src, tgt: tf.logical_and(tf.size(src) > 0, tf.size(tgt) > 0))

    if src_max_len:
        src_tgt_dataset = src_tgt_dataset.map(
            lambda src, tgt: (src[:src_max_len], tgt),
            num_threads=num_threads,
            output_buffer_size=buffer_size)
    if tgt_max_len:
        src_tgt_dataset = src_tgt_dataset.map(
            lambda src, tgt: (src, tgt[:tgt_max_len]),
            num_threads=num_threads,
            output_buffer_size=buffer_size)

    src_tgt_dataset = src_tgt_dataset.map(
        lambda src, tgt: (tf.cast(src_vocab_table.lookup(src), tf.int32),
                          tf.cast(tgt_vocab_table.lookup(tgt), tf.int32)),
        num_threads=num_threads, output_buffer_size=buffer_size)

    src_tgt_dataset = src_tgt_dataset.map(
        lambda src, tgt_in: (
            src, tgt_in, tf.size(src), tf.size(tgt_in)),
        num_threads=num_threads,
        output_buffer_size=buffer_size)

    def batching_func(x):
        return x.padded_batch(
            batch_size,
            # The first three entries are the source and target line rows;
            # these have unknown-length vectors.  The last two entries are
            # the source and target row sizes; these are scalars.
            padded_shapes=(tf.TensorShape([None]),  # src
                           tf.TensorShape([None]),  # tgt_input
                           tf.TensorShape([]),  # src_len
                           tf.TensorShape([])),  # tgt_len
            # Pad the source and target sequences with eos tokens.
            # (Though notice we don't generally need to do this since
            # later on we will be masking out calculations past the true sequence.
            padding_values=(PADDING_ID,  # src
                            PADDING_ID,  # tgt_input
                            0,  # src_len -- unused
                            0))

    def key_func(unused_1, unused_2, src_len, tgt_len):
        if src_max_len:
            bucket_width = (src_max_len + num_buckets - 1) // num_buckets
        else:
            bucket_width = 10

        bucket_id = tf.maximum(src_len // bucket_width, tgt_len // bucket_width)
        return tf.to_int64(tf.minimum(num_buckets, bucket_id))

    def reduce_func(unused_key, windowed_data):
        return batching_func(windowed_data)

    batched_dataset = src_tgt_dataset.group_by_window(
        key_func=key_func, reduce_func=reduce_func, window_size=batch_size)

    batched_iter = batched_dataset.make_initializable_iterator()
    (src_ids, tgt_input_ids, src_seq_len, tgt_seq_len) = (
        batched_iter.get_next())

    return BatchedInput(
        initializer=batched_iter.initializer,
        source=src_ids,
        target_input=tgt_input_ids,
        source_sequence_length=src_seq_len,
        target_sequence_length=tgt_seq_len)


def load_word2vec_embedding(vocab_size):
    '''
        加载外接的词向量。
        :return:
    '''
    print 'loading word embedding, it will take few minutes...'
    embeddings = np.random.uniform(-1,1,(vocab_size + 2, embeddings_size))
    # 保证每次随机出来的数一样。
    rng = np.random.RandomState(23455)
    unknown = rng.normal(size=(embeddings_size))
    padding = rng.normal(size=(embeddings_size))
    f = open(word_embedding_file)
    for index, line in enumerate(f):
        values = line.split()
        coefs = np.asarray(values[1:], dtype='float32')  # 取向量
        embeddings[index] = coefs   # 将词和对应的向量存到字典里
    f.close()
    # 顺序不能错，这个和unkown_id和padding id需要一一对应。
    embeddings[-2] = unknown
    embeddings[-1] = padding

    return tf.get_variable("embeddings", dtype=tf.float32,
                           shape=[vocab_size, embeddings_size], initializer=tf.constant_initializer(embeddings), trainable=False)


if __name__ == '__main__':
    vocab_size = get_src_vocab_size()
    src_unknown_id = tgt_unknown_id = vocab_size
    src_padding = vocab_size + 1

    src_vocab_table, tgt_vocab_table = create_vocab_tables(src_vocab_file, tgt_vocab_file, src_unknown_id, tgt_unknown_id)
    iterator = get_iterator(src_vocab_table, tgt_vocab_table, 3)
    embedding = load_word2vec_embedding()
    print 'loaded'
    x = tf.nn.embedding_lookup(embedding, [1,2,3])

    with tf.Session() as sess:
        sess.run(tf.global_variables_initializer())
        sess.run(iterator.initializer)
        tf.tables_initializer().run()

        result = sess.run(iterator.source)
        print result
        print sess.run(x).shape
