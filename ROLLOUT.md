# Rolling out the v2 platform

These are the manual steps that code can't do. Do them in this order.

## 1. Rotate the static API key (required)

The old key was committed to the iOS repo. The server now refuses that key by hash, even if it's still in `API_KEY`.

```bash
heroku config:set API_KEY=$(openssl rand -hex 32) -a <app>
```

- **Production app builds** use App Attest or Play Integrity JWTs and are not affected.
- **Dev builds** need the new key in their gitignored config:
  - iOS: `Config/Secrets.xcconfig`
  - Android: `local.properties`
- **CI** needs the new key in the `API_KEY` GitHub secret.

## 2. Deploy the web app

The release phase runs the migrations. They:
- add the new fields and tables
- turn `0,0` coordinates into `null`

## 3. Heroku add-ons and config

```bash
heroku addons:create scheduler:standard -a <app>
heroku addons:open scheduler -a <app>   # add: python manage.py run_scheduled_tasks — every 10 minutes
heroku config:set OPENAI_MODEL=gpt-5-mini -a <app>          # optional; this is the default
```

AI jobs run inside the web dyno by default. If imports get heavy, use a worker dyno instead:

```bash
heroku ps:scale worker=1
heroku config:set INGEST_RUN_INLINE=0
```

## 4. Push notifications

**iOS (APNs):**
1. In the Apple Developer portal, create a key under Keys with **Apple Push Notifications service** enabled.
2. Set:
   ```bash
   heroku config:set APNS_KEY_ID=XXXXXXXXXX APNS_AUTH_KEY="$(cat AuthKey_XXXXXXXXXX.p8)"
   ```
3. `APNS_TEAM_ID` and `APNS_TOPIC` default to `APPLE_TEAM_ID` and `APPLE_APP_BUNDLE_ID`.
4. For TestFlight and Xcode builds signed with the development `aps-environment`, also set `APNS_USE_SANDBOX=1`. Development builds are often pointed at a staging app for this reason.

**Android (FCM):**
1. Create a Firebase project linked to `com.arcu.attnd`.
2. Put `google-services.json` in `Attnd-Android/app/`. It is gitignored.
3. Set:
   ```bash
   heroku config:set FCM_PROJECT_ID=<firebase-project-id>
   ```
4. The server uses `FCM_SERVICE_ACCOUNT_JSON`, falling back to `GOOGLE_SERVICE_ACCOUNT_JSON`. That service account needs the **Firebase Cloud Messaging API Admin** role.

If a platform has no credentials, its pushes are skipped and logged. Nothing breaks.

## 5. Existing organizers

- Existing `is_staff` users can sign in at `/console/` with their current password.
- Their events keep `status=published`.
- The Django admin stays available for superusers.
