# Changelog

## 1.4.0 - 2026-11-10

PR Post now runs fully from this public repository with its private paper in Cloudflare D1 and R2, and sends its email through mailroom on Cloudflare Email Service. The Amazon SES path added in #16 was removed in #25 before this release.

### New

- Keep a private paper in Cloudflare D1 and R2 instead of git ([#15](https://github.com/n8watkins/pr-post/pull/15))
- Send the emails through Amazon SES, with bounces and complaints handled ([#16](https://github.com/n8watkins/pr-post/pull/16))
- Run the owner's paper from this public repository, with quiet jobs and on-time starts ([#17](https://github.com/n8watkins/pr-post/pull/17))
- A People page to add, approve, pause, edit and remove the people a paper follows ([#18](https://github.com/n8watkins/pr-post/pull/18))
- Papers on Cloudflare can now send their emails through

### Fixed

- A drafted profile with Windows line endings no longer looks edited after it is saved ([#19](https://github.com/n8watkins/pr-post/pull/19))

## 1.3.0 - 2026-11-09

### New

- A short email digest, a separate Who to follow email with one-click actions, writer briefs ([#13](https://github.com/n8watkins/pr-post/pull/13))

## 1.2.0 - 2026-11-09

### New

- Suggest builders to follow, with a mini report and fit score for each ([#11](https://github.com/n8watkins/pr-post/pull/11))

