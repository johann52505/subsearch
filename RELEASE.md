# Release Checklist

## Testing

- [ ] Verify add-on installs from ZIP
- [ ] Verify subtitle search works
- [ ] Verify subtitle download works
- [ ] Verify ZIP extraction works
- [ ] Verify language filtering works
- [ ] Verify subtitle saving works

## Release

- [ ] Update version in `addon.xml`
- [ ] Update README/changelog
- [ ] Create git tag
- [ ] Create GitHub Release
- [ ] Upload `service.subsearch.zip`

## Packaging

- [ ] Remove temporary files and debugging output
- [ ] Verify no personal information, API keys, credentials, or secrets are included
- [ ] Create `service.subsearch.zip`
- [ ] Verify ZIP installs on a clean Kodi 18.9 system
- [ ] Verify ZIP structure:

```text
service.subsearch/addon.xml
service.subsearch/default.py
service.subsearch/resources/settings.xml

---

JCA (2026) Rev. 1.00