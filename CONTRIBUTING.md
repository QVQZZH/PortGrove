# Contributing to Port Grove

Thanks for helping improve Port Grove. Focused bug fixes, documentation improvements, and well-scoped features are welcome.

## Before You Start

- Search existing issues before opening a new one.
- Open an issue before large behavioral or architectural changes.
- Do not include credentials, real infrastructure addresses, database files, setup tokens, logs, or private screenshots.
- Review [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before changing UI dependencies or assets.

## Development

Requirements are Linux, Python 3.10+, Node.js 18+, npm, and `ss` from `iproute2`.

```bash
npm ci
npm run check
```

For a local full-stack smoke run after the build:

```bash
AUTH_DISABLED=1 EMBEDDED_AGENT=0 npm start
```

`AUTH_DISABLED` is for local development only. Do not use it on a network-accessible deployment.

## Pull Requests

- Keep each pull request limited to one clear concern.
- Add or update tests for behavior changes.
- Update both `README.md` and `README.zh-CN.md` when user-facing setup or behavior changes.
- Run `npm run check` and `npm audit` before submission.
- Use synthetic data in screenshots and examples.

By contributing, you agree that your contribution is licensed under the repository's MIT License. Third-party material remains under its original license.
