"""Vercel entrypoint: the website in serverless mode (see fpl_advisor.web and docs/deploy.md).

Vercel finds the FastAPI `app` here and sends every request that isn't a static file in
`public/` to it, with the original path. Static files are copied into `public/` by the build
command in vercel.json, so the CDN serves them. This function holds no paid API keys: the
scheduled refresh job (GitHub Actions) does all paid work and saves a snapshot to Vercel Blob.
"""

from fastapi import FastAPI

from fpl_advisor.web import create_app

app: FastAPI = create_app(serverless=True)
