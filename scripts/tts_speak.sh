#!/usr/bin/env bash
# 文字转语音 - 调用火山引擎 TTS，支持流式播放
# 用法: ./tts_speak.sh "你好欢迎使用语音合成服务"
#       ./tts_speak.sh 你好 欢迎 使用 语音合成服务
# 可通过 run_shfile API 调用: filename=脚本路径, param=要转换的文字
#
# 前端调用示例:
#   runShFile({ filename: '/home/ztx/robot/DaoLan/scripts/tts_speak.sh', param: '你好欢迎使用语音合成服务' })

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/env.sh"

TTS_DIR="${BASE_DIR}/PythonProject/tts"
TTS_PY="${TTS_DIR}/tts.py"

# 将所有参数合并为完整文本（支持 run_shfile 传入的 param 被空格分割的情况）
TEXT="${*:-}"

if [[ -z "$TEXT" ]]; then
  echo "错误: 请提供要转换的文字" >&2
  echo "用法: $0 <要转换的文字>" >&2
  echo "示例: $0 你好欢迎使用语音合成服务" >&2
  exit 1
fi

if [[ ! -f "$TTS_PY" ]]; then
  echo "错误: TTS 脚本不存在: $TTS_PY" >&2
  exit 1
fi

# 调用火山引擎 TTS，支持流式播放（边收边播，需 ffplay）
# 若未配置 resource_id 或未安装 ffplay，则收完再播
cd "$TTS_DIR"
exec python3 "$TTS_PY" "$TEXT"
