#!/usr/bin/env bash
# Clean-container ## Verify — BOARD slice (card 33de4ec059). Debian-12, NOTHING pre-existing.
# Exit code is the truth: 0 iff owner-lifecycle (SQLite) + board gates 25b/25c all pass.
set -eu
echo "### CLEAN-CONTAINER BOARD VERIFY — $(cat /etc/debian_version 2>/dev/null) ###"
export DEBIAN_FRONTEND=noninteractive
echo "-- installing python3 + git (fresh base) --"
apt-get update -qq >/dev/null
apt-get install -y -qq python3 git >/dev/null
python3 --version; git --version

# fresh install dir — nothing pre-existing
export INSTALL_DIR=/opt/mp-node
rm -rf "$INSTALL_DIR"; mkdir -p "$INSTALL_DIR"/bin "$INSTALL_DIR"/todos "$INSTALL_DIR"/run "$INSTALL_DIR"/status
cp /harness/bin/* "$INSTALL_DIR"/bin/
chmod +x "$INSTALL_DIR"/bin/board-restore "$INSTALL_DIR"/bin/todo-server.py "$INSTALL_DIR"/bin/board-exporter.py

# queue.env as a seed-generated install bakes it: BOARD_BACKEND=sqlite
mkdir -p /root/.config/mypeople
cat > /root/.config/mypeople/queue.env <<EOF
export INSTALL_DIR="$INSTALL_DIR"
export HOST_ID="verifynode"
export QUEUE_SECRET="test-secret"
export HUD_PORT="9900"
export TODO_PORT="9933"
export TTYD_PORT="7681"
export QUEUE_URL="http://127.0.0.1:9900"
export BOARD_BACKEND="sqlite"
EOF
export MYPEOPLE_CONFIG_PATH=/root/.config/mypeople/queue.env

echo
echo "===== GATE A: board acceptance (owner lifecycle) on the SQLite store ====="
MYPEOPLE_BOARD_BACKEND=sqlite python3 /harness/test_owner_lifecycle.py \
  --runtime "$INSTALL_DIR"/bin/todo-server.py \
  --ui "$INSTALL_DIR"/bin/todos.html \
  --culture /harness/boss-culture-v6.md

echo
echo "===== GATE B: §15 board gates 25b + 25c against the SQLite store ====="
python3 /harness/board_gates.py

# persist the harness on disk (H2): the node keeps a re-runnable verify
mkdir -p "$INSTALL_DIR"/verify
cp /harness/board_gates.py /harness/verify.sh "$INSTALL_DIR"/verify/ 2>/dev/null || true

echo
echo "VERIFY_BOARD_OK"
