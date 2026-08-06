#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PHANTOM_DIR="${PHANTOM_DIR:-$HOME/Phantom}"

echo "==> Installing Phantom into $PHANTOM_DIR"
if [ ! -d "$PHANTOM_DIR/.git" ]; then
  git clone https://github.com/Phantom-video/Phantom.git "$PHANTOM_DIR"
fi

cd "$PHANTOM_DIR"
python -m pip install -U pip
pip install -r requirements.txt
pip install "huggingface_hub[cli]"

echo "==> Downloading Wan2.1 base weights (large download)"
huggingface-cli download Wan-AI/Wan2.1-T2V-1.3B --local-dir "$PHANTOM_DIR/Wan2.1-T2V-1.3B"

echo "==> Downloading Phantom-Wan weights"
huggingface-cli download bytedance-research/Phantom --local-dir "$PHANTOM_DIR/Phantom-Wan-Models"

echo
echo "Done. Set these in davinci-phantos/.env:"
echo "PHANTOM_ROOT=$PHANTOM_DIR"
echo "WAN_CKPT_DIR=$PHANTOM_DIR/Wan2.1-T2V-1.3B"
echo "PHANTOM_CKPT=$PHANTOM_DIR/Phantom-Wan-Models/Phantom-Wan-1.3B.pth"
