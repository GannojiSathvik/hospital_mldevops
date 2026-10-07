## Summary

<!-- What does this PR change and why? Link the issue: Closes #123 -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Model / data change (retraining, new features, new params)
- [ ] Infrastructure / CI change
- [ ] Documentation

## Testing

- [ ] `make test` passes locally
- [ ] `make lint` and `make type-check` pass
- [ ] New/changed behaviour is covered by tests

## Security checklist

- [ ] No secrets, API keys, tokens or credentials are committed (`make security-scan` / gitleaks clean)
- [ ] No real patient data or PII — only synthetic/anonymised data
- [ ] New inputs are validated (Pydantic schema / upload size / file type)
- [ ] Errors do not leak stack traces or configuration to API clients
- [ ] New endpoints are protected by the correct RBAC permission (`configs/access_control.yaml`)
- [ ] New dependencies are pinned and pass `pip-audit`
- [ ] Docker/K8s/Terraform changes keep non-root, read-only FS, least privilege (Checkov clean)
- [ ] GitHub workflow changes keep `permissions: contents: read` by default and elevate per job only

## Model changes (if applicable)

- [ ] Metrics compared with the current production model (`models/metrics.json`)
- [ ] Recall / PR-AUC not degraded beyond the approved tolerance
- [ ] Model card / data card updated

## Healthcare disclaimer

- [ ] This change does not present the model as a clinical decision tool
