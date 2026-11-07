# 08 · Integrating with a host app

The engine is built as a standalone module, separate from any host codebase. This page lists the exact changes your developer makes to connect it.

```
food-service/food/                         ──copy──►  host-backend/app/services/food/
food-service/flutter/lib/features/food_log ──copy──►  app/lib/features/food_log/
```

---

## Part A · Backend (host-backend)

### A1. Copy the module
Copy the whole `food-service/food/` folder (including `food/data/foods.json` and `aliases.json`) to `host-backend/app/services/food/`.
- The Dockerfile already does `COPY app /app/app`, and `.dockerignore` doesn't exclude it, so the JSON data ships automatically.
- No new Python packages are needed: FastAPI, httpx, motor and boto3 are already in `requirements.txt`.
- The host backend runs **Python 3.12**; the module avoids newer syntax.

### A2. Register the routes: `app/gateway.py`
Next to the other imports:
```python
from app.services.food.api import admin_router as food_admin_router
from app.services.food.api import router as food_router
```
Next to the other `app.include_router(...)` lines (around line 365–404):
```python
    app.include_router(food_router)
    app.include_router(food_admin_router)
```

### A3. Allow the MongoDB collections
`get_allowed_collection` refuses collections that aren't listed. Add these 7 names to the **`MONGODB_ALLOWED_COLLECTIONS`** environment variable (comma-separated) in every environment (dev, prod):
```
food_analyses,food_meal_logs,food_corrections,food_user_profiles,food_aliases,food_review_queue,food_overrides
```

### A4. Environment variables (ECS task definition)

| Variable | Value | Notes |
|---|---|---|
| `GEMINI_API_KEY` | from Google AI Studio (**paid tier**) | Store in AWS Secrets Manager / SSM like other secrets. **Never in the app.** |
| `FOOD_AUTH_MODE` | `host` | already the default; set it explicitly anyway |
| `FOOD_STORE` | `mongo` | already the default |
| `MONGODB_ALLOWED_COLLECTIONS` | existing list + the 7 above | |
| *optional* `FOOD_STORE_DETAILS` | `false` (default) | privacy mode: no learning data, meals auto-deleted after `FOOD_HISTORY_DAYS` (30). `true` turns learning on |
| *optional* `FOOD_USE_DATABASE` | `false` (default) / `true` | `false` = AI estimates only; `true` = INDB lookup + AI fallback |
| *optional* `FOOD_AI_PROVIDER` | `bedrock` | to use Claude via Bedrock instead (see A7) |
| *optional* `FOOD_RATE_LIMIT_PER_MINUTE` / `_PER_DAY` | `10` / `150` | |

**Never set `FOOD_AUTH_MODE=dev` on a server**: it accepts fake tokens.

### A5. Indexes (performance)
Add to `create_indexes.py` (same style as the existing ones), or run once in `mongosh`:
```javascript
db.food_meal_logs.createIndex({ userId: 1, createdAt: -1 })
db.food_analyses.createIndex({ userId: 1, createdAt: -1 })
db.food_corrections.createIndex({ aiKey: 1, kind: 1 })
db.food_corrections.createIndex({ userId: 1, kind: 1 })
db.food_review_queue.createIndex({ status: 1, createdAt: -1 })
```
`food_aliases`, `food_overrides` and `food_user_profiles` are looked up by `_id` only.

**Auto-delete (TTL) indexes** on `expireAt` for `food_meal_logs` and `food_analyses` are created by the module itself the first time it saves. If the database user can't create indexes, add them to `create_indexes.py`:
```javascript
db.food_meal_logs.createIndex({ expireAt: 1 }, { expireAfterSeconds: 0 })
db.food_analyses.createIndex({ expireAt: 1 }, { expireAfterSeconds: 0 })
```

**Account deletion:** in the host app's existing account-deletion code, add:
```python
from app.services.food.api import get_engine
await get_engine().delete_user_data(normalized_phone)
```

### A6. Check it works (dev stack)
1. Deploy to dev and open `<dev-backend>/docs`. You should see **app-food-log** and **admin-food-log**.
2. With a real dev app token:
   ```bash
   curl -X POST <dev-backend>/api/auth/food/analyze \
     -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
     -d '{"phoneNumber":"<that user phone>","text":"2 idli with sambar"}'
   ```
3. Expect items with `"source": "ai_estimate"` (or `"database"` when `FOOD_USE_DATABASE=true`). A `403 "Collection ... is not allowed"` means A3 was missed.

### A7. Optional: Claude on Bedrock instead of Gemini
The host backend can already call Bedrock (`anthropic_version: bedrock-2023-05-31`).
- **To switch:** set `FOOD_AI_PROVIDER=bedrock`, `FOOD_BEDROCK_MODEL_ID=<your Bedrock model id>` and `FOOD_BEDROCK_REGION=ap-south-1`.
- **IAM:** the ECS task role needs `bedrock:InvokeModel`; make sure it covers that model id.
- **Limitation:** Bedrock Claude takes photo + text, but **not audio**, which is fine because the app does speech-to-text on the phone.
- ⚠️ This client is written but **not yet run**. Test it on dev first.

### A8. Optional: entitlement gate
Face scan checks a subscription entitlement (`_require_face_scan_entitlement`). To make food scan a paid feature, add a similar check at the top of `analyze()` in `food/api.py`, after `verify_app_user`.

### A9. Optional: shared rate limit
`food/ratelimit.py` counts per process. With 2 uvicorn workers × N ECS tasks, a user can make up to 2N × the limit. the host backend's `app/core/security.py` already has a Redis-based limiter (line ~84); swap it in for exact limits.

---

## Part B · Flutter app (host app)

### B1. Copy the screens
Copy `food-service/flutter/lib/features/food_log/` to `app/lib/features/food_log/`. It contains:
- `data/food_log_api_client.dart`
- `presentation/food_log_screen.dart`
- `presentation/food_review_screen.dart`
- `presentation/food_diary_screen.dart`

### B2. Add the speech-to-text package: `pubspec.yaml`
```yaml
dependencies:
  speech_to_text: ^7.0.0   # check pub.dev for the latest version
```
`image_picker`, `http` and `permission_handler` are already there. Then run `flutter pub get`.

### B3. Permissions

**iOS:** `ios/Runner/Info.plist`. Camera and microphone descriptions already exist (lines 49–51). **Add:**
```xml
<key>NSSpeechRecognitionUsageDescription</key>
<string>The app turns what you say into text so you can log meals by voice.</string>
```

**Android:** `android/app/src/main/AndroidManifest.xml`. `CAMERA` and `RECORD_AUDIO` already exist. Inside the existing `<queries>` block (line ~85), **add:**
```xml
<intent>
    <action android:name="android.speech.RecognitionService" />
</intent>
```

### B4. Add a menu entry
In `lib/features/home/presentation/v2/_v2_menu_drawer.dart`, in the nutrition section next to "Talk to Nutritionist" (around line 622):
```dart
_SubMenuActionItem(
  title: 'Log a meal',            // move to l10n (app_en.arb etc.) like the others
  onTap: () {
    Navigator.of(context).pop();
    Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => const FoodLogScreen()),
    );
  },
),
```
with `import 'package:calories_tracker/features/food_log/presentation/food_log_screen.dart';`. Add the PostHog `service_entry_tapped` event the same way the neighbouring items do.

### B5. Compile and check
```bash
flutter pub get
flutter analyze lib/features/food_log
flutter run
```
⚠️ These Dart files were written to match the host app's code (`AppColors`, `AppSpacing`, `AuthSessionService`, `AppSessionAutoRefreshClient`, `AppConfig`, `AuthSessionGuard`) but **were not compiled**, because there's no Flutter SDK on the machine they were written on. Expect possibly a few small fixes from `flutter analyze`.

### B6. Text for translation
The screens use English strings directly. The host app has l10n (en, es, ja, ko, zh), so move strings into the `.arb` files when polishing.

---

## Part C · Later improvements in the host app

| Idea | Where |
|---|---|
| Let an AI coach read the food diary (calorie/protein totals vs goal) | your coach service |
| Show daily calories eaten next to "active calories burned" | home dashboard |
| Nutritionist review queue screen | the admin app, calling `/api/admin/food/review-queue` |
| Let booked nutritionists see a client's food diary (with consent) | nutrition feature |

## Rollback
To switch the feature off without a deploy, hide the menu entry with a feature flag (e.g. a `lib/core/config/feature_flags.dart`). To fully remove it on the backend, delete the two `include_router` lines. Nothing else in the host app depends on the module.
