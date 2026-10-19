#!/bin/bash
probe(){
  printf '%-22s ' "$1"
  curl -s -o /tmp/probe.out -w '%{http_code} %{time_total}s ' --max-time 8 "$2" -H 'Content-Type: application/json' $3
  head -c 150 /tmp/probe.out | tr '\n' ' '
  echo
}
Q='The old harbor was rebuilt twice after the war.'
J="{\"q\":\"$Q\",\"source\":\"en\",\"target\":\"zh\"}"
probe gtx "https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=zh-CN&dt=t&q=$Q"
probe libre_de "https://libretranslate.de/translate" "-d $J"
probe terraprint "https://translate.terraprint.co/translate" "-d $J"
probe fedilab "https://translate.fedilab.app/translate" "-d $J"
probe ms_search "https://www.modelscope.cn/api/v1/dolphin/models?PageNumber=1&PageSize=5&Name=nllb"
