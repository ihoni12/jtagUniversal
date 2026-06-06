#!/bin/bash
cd "$(dirname "$0")"
npm install
npm run dev -- --host 0.0.0.0
