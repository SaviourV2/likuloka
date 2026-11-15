#!/bin/sh
# Wraps app.html (the page body) in a full HTML document so it can be hosted anywhere.
cd "$(dirname "$0")"
{
  printf '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n<style>html,body{margin:0}img{max-width:100%%}[hidden]{display:none!important}</style>\n</head>\n<body>\n'
  cat app.html
  printf '</body>\n</html>\n'
} > index.html
