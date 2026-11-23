import argparse
import hashlib
import json
import logging
import math
import os
import tempfile
import threading
import time
from functools import lru_cache
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

MODEL_NAME = 'sentence-transformers/all-MiniLM-L6-v2'
MODEL_REVISION = 'd13954661f83248295ba75c1ed411eef3b7b936e'
MODEL_VERSION = MODEL_NAME + ':' + MODEL_REVISION
RERANKER_NAME = 'Xenova/ms-marco-TinyBERT-L-2-v2'
RERANKER_REVISION = 'b76bb5e1fefd66aa36cd108622d768e86c015ff1'
RERANKER_VERSION = RERANKER_NAME + ':' + RERANKER_REVISION + ':quantized'
RELEVANCE_SCORE_KIND = 'uncalibrated_logit'
MODEL_FILES = {
    'model.onnx': (90387630, 'bbd7b466f6d58e646fdc2bd5fd67b2f5e93c0b687011bd4548c420f7bd46f0c5'),
    'config.json': (650, '1b4d8e2a3988377ed8b519a31d8d31025a25f1c5f8606998e8014111438efcd7'),
    'special_tokens_map.json': (695, '5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a'),
    'tokenizer.json': (711649, '851ca67100d372ca3ae031a6abd168f53489eebfd7d89523f35c5c9b4d372c3c'),
    'tokenizer_config.json': (1412, 'abda01c8c14c5151ae498aceb30db406d6b91242c394fd88d3b6fd5a63a101e6'),
    'vocab.txt': (231508, '07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3'),
}
RERANKER_FILES = {
    'onnx/model_quantized.onnx': (4496298, '026c2ec3257cd351696e45bbd6040bb83cf818ba89059b4344bd6350138b62ce'),
    'config.json': (824, '463d8bce12140e74c56e008b1212b056487a76acdca8f31f9627d202f6e5a7bd'),
    'special_tokens_map.json': (125, 'b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3'),
    'tokenizer.json': (711396, 'd241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66'),
    'tokenizer_config.json': (1242, '0b29c7bfc889e53b36d9dd3e686dd4300f6525110eaa98c76a5dafceb2029f53'),
    'vocab.txt': (231508, '07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3'),
}
MODEL_PATH = Path(os.environ.get('INSTRUCTION_MODEL_PATH', str(Path.home() / '.cache' / 'guest-recovery' / MODEL_REVISION)))
MODEL_LOCK = threading.RLock()
_model = None
_reranker = None


def prepare_artifacts(repository, revision, directory, artifacts):
    directory.mkdir(parents=True, exist_ok=True)
    for name, (size, expected_hash) in artifacts.items():
        destination = directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and destination.stat().st_size == size:
            with destination.open('rb') as existing:
                if hashlib.file_digest(existing, 'sha256').hexdigest() == expected_hash:
                    continue
        for attempt in range(3):
            filename = None
            try:
                digest = hashlib.sha256()
                received = 0
                deadline = time.monotonic() + 120
                url = 'https://huggingface.co/' + repository + '/resolve/' + revision + '/' + name
                with urlopen(url, timeout=20) as response, tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
                    filename = output.name
                    while chunk := response.read(256 * 1024):
                        received += len(chunk)
                        if received > size or time.monotonic() > deadline:
                            raise TimeoutError('Instruction model download exceeded its size or time limit')
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if received != size or digest.hexdigest() != expected_hash:
                    raise ValueError('Instruction model artifact integrity check failed')
                os.chmod(filename, 0o644)
                os.replace(filename, destination)
                break
            except (URLError, TimeoutError, ConnectionError, IncompleteRead) as error:
                if isinstance(error, HTTPError) and error.code != 429 and error.code < 500:
                    raise
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
            finally:
                if filename and os.path.exists(filename):
                    os.unlink(filename)


def prepare_model():
    prepare_artifacts('Qdrant/all-MiniLM-L6-v2-onnx', MODEL_REVISION, MODEL_PATH, MODEL_FILES)
    prepare_artifacts(RERANKER_NAME, RERANKER_REVISION, MODEL_PATH / 'reranker', RERANKER_FILES)
    print(json.dumps({'event': 'instruction_models_prepared', 'embedding': MODEL_VERSION, 'reranker': RERANKER_VERSION}))


def verify_artifacts(directory, artifacts):
    for name, (size, expected_hash) in artifacts.items():
        artifact = directory / name
        if not artifact.is_file() or artifact.stat().st_size != size:
            raise RuntimeError('Prepare the instruction models with python instruction_search.py --prepare-model')
        with artifact.open('rb') as existing:
            if hashlib.file_digest(existing, 'sha256').hexdigest() != expected_hash:
                raise RuntimeError('Instruction model is corrupt; run python instruction_search.py --prepare-model')


def encode(texts):
    global _model
    documents = list(texts)
    if not documents:
        return []
    if any(not isinstance(text, str) or not text.strip() for text in documents):
        raise ValueError('Semantic search requires nonempty text')
    with MODEL_LOCK:
        if _model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as error:
                raise RuntimeError('Install requirements.txt before using instruction semantic search') from error
            verify_artifacts(MODEL_PATH, MODEL_FILES)
            _model = TextEmbedding(model_name=MODEL_NAME, specific_model_path=str(MODEL_PATH), cache_dir=str(MODEL_PATH), local_files_only=True, threads=2, providers=['CPUExecutionProvider'], enable_cpu_mem_arena=False)
            logging.getLogger(__name__).info(json.dumps({'event': 'instruction_model_loaded', 'model': MODEL_VERSION}))
        vectors = list(_model.embed(documents, batch_size=16, parallel=None))
    if len(vectors) != len(documents):
        raise ValueError('Instruction model returned the wrong number of embeddings')
    normalized = []
    for vector in vectors:
        if vector.shape != (384,):
            raise ValueError('Instruction model returned an invalid embedding dimension')
        vector = vector.astype('float32')
        norm = float(vector.dot(vector)) ** 0.5
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError('Instruction model returned an invalid embedding')
        vector /= norm
        vector.flags.writeable = False
        normalized.append(vector)
    return normalized


@lru_cache(maxsize=512)
def query_embedding(query):
    return encode([query])[0]


def similarities(query, documents):
    vector = query_embedding(query)
    return [float(vector.dot(document)) for document in encode(documents)]


def relevance(query, passages):
    global _reranker
    documents = list(passages)
    if not documents:
        return []
    if not isinstance(query, str) or not query.strip() or any(not isinstance(text, str) or not text.strip() for text in documents):
        raise ValueError('Relevance scoring requires nonempty text')
    with MODEL_LOCK:
        if _reranker is None:
            try:
                from fastembed.common.model_description import ModelSource
                from fastembed.rerank.cross_encoder import TextCrossEncoder
            except ImportError as error:
                raise RuntimeError('Install requirements.txt before using instruction relevance scoring') from error
            directory = MODEL_PATH / 'reranker'
            verify_artifacts(directory, RERANKER_FILES)
            if not any(model['model'] == RERANKER_NAME for model in TextCrossEncoder.list_supported_models()):
                TextCrossEncoder.add_custom_model(model=RERANKER_NAME, sources=ModelSource(hf=RERANKER_NAME), model_file='onnx/model_quantized.onnx', license='apache-2.0', size_in_gb=0.005)
            _reranker = TextCrossEncoder(model_name=RERANKER_NAME, specific_model_path=str(directory), cache_dir=str(directory), local_files_only=True, threads=2, providers=['CPUExecutionProvider'], enable_cpu_mem_arena=False)
            logging.getLogger(__name__).info(json.dumps({'event': 'instruction_reranker_loaded', 'model': RERANKER_VERSION, 'score_kind': RELEVANCE_SCORE_KIND}))
        scores = list(_reranker.rerank(query, documents, batch_size=16))
    if len(scores) != len(documents) or any(not math.isfinite(score) for score in scores):
        raise ValueError('Instruction reranker returned invalid scores')
    return scores


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-model', action='store_true', required=True)
    parser.parse_args()
    prepare_model()
