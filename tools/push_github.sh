#!/usr/bin/env bash
# 一键推送 TST 仓库到 GitHub（项目完工后再跑，不要提前推）
#
# 用法：
#   export GH_USER=你的GitHub用户名
#   bash tools/push_github.sh
#
# 首次推送会要求输入：
#   用户名 = GH_USER
#   密码   = Personal Access Token（不是登录密码！GitHub 2021 起禁用密码推送）
#            Token 权限勾选 repo（public 仓库也需 repo scope 或 fine-grained 的 Contents: Write）
#
# 脚本不会保存 Token，也不会强推（禁止 --force）。
set -euo pipefail

REPO_NAME="${REPO_NAME:-trade-settlement-trace-f25015243}"
GH_USER="${GH_USER:-}"
PROXY="${PROXY:-http://127.0.0.1:57641}"   # 本机代理；直连能通可置空: PROXY=""

cd "$(dirname "$0")/.."

echo "==> 仓库目录: $(pwd)"
echo "==> 目标仓库: github.com/${GH_USER:-<未设置 GH_USER>}/${REPO_NAME}"

# 0. 干净检查
if [ -n "$(git status --porcelain)" ]; then
  echo "[!] 有未提交的改动，先提交再推送："
  git status --short
  exit 1
fi

# 1. 分支对齐（GitHub 默认 main）
if [ "$(git branch --show-current)" != "main" ]; then
  echo "==> 分支 $(git branch --show-current) -> main"
  git branch -M main
fi

# 2. 远端
if ! git remote get-url origin >/dev/null 2>&1; then
  if [ -z "$GH_USER" ]; then
    echo "[x] 未配置 origin，且没有 GH_USER。请先 export GH_USER=你的用户名"
    exit 1
  fi
  echo "==> 添加远端 origin"
  git remote add origin "https://github.com/${GH_USER}/${REPO_NAME}.git"
else
  echo "==> 已有远端: $(git remote get-url origin)"
fi

# 3. 代理（网络慢时用；通了可不管）
if [ -n "$PROXY" ]; then
  git config http.proxy "$PROXY"
  git config https.proxy "$PROXY"
  echo "==> 已为 git 配置代理 $PROXY（可用 git config --unset http.proxy 取消）"
fi

# 4. 推送
echo "==> push 中…… 若卡住，检查网络/代理，或改用网页拖拽上传"
git push -u origin main

echo "==> 完成: https://github.com/${GH_USER}/${REPO_NAME}"
