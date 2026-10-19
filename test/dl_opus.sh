#!/bin/bash
set -u
REPO="Helsinki-NLP/opus-mt-en-zh"
DEST="models/opus-mt-en-zh"
mkdir -p "$DEST"
BASE="https://www.modelscope.cn/api/v1/models/$REPO/repo?Revision=master&FilePath="
for f in config.json pytorch_model.bin source.spm target.spm vocab.json tokenizer_config.json generation_config.json special_tokens_map.json; do
  printf '%-26s ' "$f"
  curl -s -L --max-time 900 -o "$DEST/$f" "${BASE}$f" -w '%{http_code} %{size_download} bytes\n'
done
ls -l "$DEST"
