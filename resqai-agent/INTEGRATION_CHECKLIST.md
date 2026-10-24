# Integration checklist

- [ ] Configure a current, valid hosted scenario embed URL locally.
- [ ] Confirm the scenario is still private/public as intended.
- [ ] Confirm the link/token expiry and provider billing/usage limits.
- [ ] Integrate the widget into the existing SOS screen (do not add a duplicate SOS flow).
- [ ] Check browser microphone permissions and autoplay policies.
- [ ] Test desktop Chrome and a mobile browser.
- [ ] Test expired/invalid token and offline/network failure.
- [ ] Ensure secrets and `.env*` files are gitignored.
- [ ] Verify the emergency disclaimer is visible.
- [ ] Verify no false success claims for GPS, contacts, or dispatch.
- [ ] Run the repository's tests, typecheck, lint, and production build.
- [ ] For production, replace temporary URL handling with a documented server-side token/session flow if supported.
