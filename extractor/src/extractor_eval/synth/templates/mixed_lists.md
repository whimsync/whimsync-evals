---
description: Bulleted and numbered lists mixed with prose.
---
## Release Readiness

Before a release is approved the following conditions must hold:

- All integration tests pass on the release branch
- No open severity one bugs remain
- Documentation has been reviewed by the docs team

The release process itself follows these steps:

1. Tag the release commit with the version number
2. Build signed artifacts in the release pipeline
3. Publish artifacts to the package registry
4. Announce the release in the engineering channel
