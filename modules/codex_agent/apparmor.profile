# Keep Ubuntu's unprivileged-userns policy local to this module; do not change sysctls.
profile opscenter-codex-userns flags=(unconfined) {
  userns,
}
