#!/bin/zsh
# Start both Flask backend and React frontend dev server

BASE="$(cd "$(dirname "$0")" && pwd)"

echo "Starting Flask backend on :5000 ..."
source ~/.zshrc
cd "$BASE/backend"
python3 app.py &
FLASK_PID=$!

echo "Starting React frontend on :3000 ..."
cd "$BASE/frontend"
npm run dev &
VITE_PID=$!

echo ""
echo "Backend  → http://localhost:5000"
echo "Frontend → http://localhost:3000"
echo ""
echo "Press Ctrl+C to stop both servers."

trap "kill $FLASK_PID $VITE_PID 2>/dev/null; exit" INT TERM
wait
