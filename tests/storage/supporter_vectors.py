"""Test-only Ed25519 Supporter Key vectors (stdlib-only, no pytest dependency).

Generated via Node WebCrypto using port-light-page/functions/_lib/supporter.js
with throwaway test keypairs (never production keys). Shared by Python unit
tests and the Playwright browser smoke suite running in CI without pytest.
"""

from __future__ import annotations

TEST_PUB1_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEANG45UzRcpXN5W2GGR2iCtJKtv6l78qJ5azZMz6mEyJc=\n"
    "-----END PUBLIC KEY-----"
)
TEST_PUB2_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEAzitIufXJnQAZGOt9OZCfWbEb4L4nn0QC8OB0U4nW7jE=\n"
    "-----END PUBLIC KEY-----"
)

PERSONAL_KEY = (
    "PL1.eyJpYXQiOiIyMDI2LTExLTI1Iiwib2lkIjoib3JkXzU1NTQ0NDMzMyIsInRpZXIiOiJwZXJzb25hbCIsInYiOjF9."
    "j9cC3q_BhXLQO0QnBy9X-NAmvSh0hv9Y9C_5lpgd78icxtPmHRkWdfpPNZOV7SIM_tARYuulAlaRHDA9TmH4Bw"
)
FLEET_KEY = (
    "PL1.eyJpYXQiOiIyMDI2LTExLTI1Iiwib2lkIjoib3JkX2ZsZWV0Xzk4NzY1NCIsInRpZXIiOiJmbGVldCIsInYiOjF9."
    "dfYGfIwQ9x1MsdKEiFdzWs9uSutR6NtXHw6m45pGa6oFF1CBEuMZpxt6Y6gh9k8ARd8PoznPmk8QGxdkZJgmBw"
)
ROTATED_KEY = (
    "PL1.eyJpYXQiOiIyMDI2LTEyLTAxIiwib2lkIjoib3JkX3JvdF8wMDEiLCJ0aWVyIjoicGVyc29uYWwiLCJ2IjoxfQ."
    "cmyBZUXNq_9yvSMD7CWJj85gCDBXd8-R68xIKfDg_WxW-9gboc0fGEwDZRqixg4cn0g3aOpeMNhwIe5hwVmDAA"
)
EXTRA_FIELD_KEY = (
    "PL1.eyJleHRyYSI6ImZ1dHVyZSIsImlhdCI6IjIwMjYtMTEtMjUiLCJvaWQiOiJvcmRfZXh0cmFfOTkiLCJ0aWVyIjoicGVyc29uYWwiLCJ2IjoxfQ."
    "O1DKd2Fpj1_z7QmksBjwdbU0zNXe5EBdztuffeZGlSUYlQZUUkYORZDazBKp8t3vSblzHQxydMgPSurjn7XVDg"
)
