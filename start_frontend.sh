#!/bin/bash
cd "$(dirname "$0")/frontend"
npm install
npm run dev -- --host 0.0.0.0
