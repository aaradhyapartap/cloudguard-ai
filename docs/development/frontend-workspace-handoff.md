# CloudGuard AI — Frontend Workspace Handoff

**Branch:** `feat/frontend-workspace` (branched from `origin/main` `8a821025c06cddd89ac972c8302a202c1aebb445`)
**Static Export Compatibility:** Preserved (`output: 'export'`, `trailingSlash: true`, builds to `frontend/out/`)

---

## 1. Design Direction & Architecture

- **Visual System (`frontend/src/app/globals.css`)**: Deep navy/graphite navigation rail (`#0f172a`), warm ivory canvas (`#faf8f5`), crisp white surfaces (`#ffffff`), restrained emerald primary accent (`#047857`), ordered severity ramp (`critical`, `high`, `medium`, `low`), IBM Plex Sans/Mono typography, visible `:focus-visible` outlines, and `prefers-reduced-motion` support.
- **Accessible Navigation & Shell (`Sidebar.tsx`, `TopBar.tsx`, `WorkspaceShell.tsx`)**: Responsive desktop rail and mobile drawer menu with `Escape` dismissal, `Tab`/`Shift+Tab` focus containment, desktop-breakpoint (`min-width: 768px`) auto-close/reset via `matchMedia`, removal of background `inert` + `aria-hidden` before focus restoration (plus unmount cleanup), and removal of stale phase-scaffold labels (`Phase N`, `Phase 2 is running`) and unbacked routes (`Investigations`, `Audit Log`). Navigation visibility is derived from `/api/v1/me` permissions via `AuthProvider.can(...)`.
- **Auth & Security Preserved**: Cognito PKCE, `state`/`nonce` validation, single-flight callback exchange, logout guards, bearer token injection via `api-client.ts`, automatic `401` session clearing, and plain-text rendering (no `dangerouslySetInnerHTML`).

---

## 2. Hardening Fixes Applied

1. **Document Upload Correctness & File Binding (`documents/page.tsx`)**:
   - Added file selection (`<input type="file">`) populating actual file metadata (`name`, `type`, `size`) and capturing an immutable file-to-registration snapshot (`file`, `boundFilename`, `boundContentType`, `boundSizeBytes`) on registration so selecting another file afterward cannot `PUT` the wrong file to an earlier registration's destination.
   - Disabled file input and re-registration controls while registering/uploading to prevent replacement races.
   - Presigned `http://`/`https://` URLs upload bytes directly via `PUT` **without** `Authorization` bearer credentials; failed uploads surface the HTTP error and never call `/complete`.
   - Local `memory://` registration mode (`DOCUMENT_STORE=memory`) explicitly states it is metadata registration only, cannot accept browser byte uploads, and returns `404` if `/complete` is called without uploaded bytes.
2. **Approval Selection & Decision Safety (`risks/page.tsx`)**:
   - Tracks `requestedApprovalId` independently from loaded `selectedApproval`, immediately clearing previous actionable detail and decision/replacement-action fields on selection so a failed detail load shows an error + `Retry loading approval` button with **no** decision form for the previous approval.
   - Guards both detail fetches (`approvalRequestSeqRef`) and decision submissions (`decisionSeqRef`, `requestedApprovalIdRef`) against subsequent selection changes so pending responses/errors/loading updates cannot overwrite a newly selected approval.
   - Recording a decision preserves the `DecidedApproval` view, refreshes `/approvals` without overwriting the recorded decision, and states that downstream execution is asynchronous and not yet marked succeeded.
3. **Assistant Request Lifecycle & Nullable Usage (`assistant/page.tsx`, `types.ts`)**:
   - `activeRequestIdRef` guards all response, error, and loading state updates so cancelled or older out-of-order responses never overwrite a newer query.
   - Rendered answers stay explicitly associated with their submitted question (`Question asked: ...`).
   - `TokenUsage` (`input_tokens: number | null`, `output_tokens: number | null`) renders `unknown` when `null`/`undefined` and preserves `0` as `0`.
4. **Honest Dashboard State (`dashboard/page.tsx`)**:
   - Distinguishes `loading`, `ready` (including explicit `0`), and `error` (`Unavailable`) per widget.
   - Labels paginated list counts accurately (`5+ (first page)` / `10+ (first page)`) rather than presenting truncated slices as organization totals.

---

## 3. Verification Matrix (Mocked vs. Live vs. Blocked)

### Mocked Unit & Component Tests (`vitest run`)
- `npm run lint` (`eslint src --max-warnings 0`): passed (0 warnings/errors).
- `npm run typecheck` (`tsc --noEmit`): passed (0 errors).
- `npm test` (`vitest run`): **8 test files, 31 tests passed**:
  - `AuthProvider.test.tsx` (6 tests): PKCE, state/nonce, single-flight exchange, logout guards.
  - `analytics/page.test.tsx` (2 tests): Manager evaluation run trigger, Admin read-only enforcement.
  - `Sidebar.test.tsx` (5 tests): Analyst/Admin nav scoping, mobile drawer `Escape` dismissal, `Tab`/`Shift+Tab` focus containment, focus restoration, `inert` background, and desktop-breakpoint resize auto-close.
  - `documents/page.test.tsx` (5 tests): `memory://` 404 missing-object behavior, presigned `PUT` without `Authorization` before `/complete`, failed `PUT` skipping `/complete`, file-to-registration binding (register A -> select B -> `PUT` sends A), Admin read-only.
  - `assistant/page.test.tsx` (4 tests): RAG citations, cancel-then-resubmit & out-of-order guard, nullable/partial/zero/complete `TokenUsage`, Admin `ai:query` denial.
  - `risks/page.test.tsx` (5 tests): clearance omission notice + Manager finalize/override, approval A -> B field reset & out-of-order stale response guard on `modified` decision, A selected -> B detail fails clears A with retry & no decision form, A pending decision -> selecting B guarded against stale result, Admin read-only.
  - `dashboard/page.test.tsx` (2 tests): zero vs unavailable/error states, limited-list labeling.
  - `settings/page.test.tsx` (2 tests): `/system/config` providers/flags for Admin, denial without `settings:manage`.
- `npm run build` (`next build`): passed, exporting 11/11 static pages to `frontend/out/`.

### Live Local Backend & Real-Browser Checks (`http://localhost:3000` + `http://127.0.0.1:8000`)
- **Analyst (`analyst@acme.test`)**: Signed in, verified `/dashboard`, registered synthetic document on `/documents` and observed real `404` (`The uploaded document object does not exist. (status 404)`) on `memory://` completion, queried `/assistant` (`mock:chat-v1`), opened mobile drawer at `390x844`, dismissed with `Escape` (verified `focusRestoredAfterEsc: true`, `backgroundInertRemovedAfterEsc: true`), and re-opened on mobile then resized to `1440x900` (verified `drawerClosedOnDesktopResize: true`, `backgroundInertRemovedOnDesktopResize: true`).
- **Admin (`admin@acme.test`)**: Verified `AI Assistant` omitted from nav and `/settings` populated from `/api/v1/system/config`.
- **Manager (`manager@acme.test`)**: Created synthetic compliance assessment (`Synthetic UI Verification Review` against seeded framework `edada091-0d12-4ff2-b093-97e4af5ef797`), computed deterministic score, recorded Manager score override (`88.50`), finalized assessment (`completed`), inspected the `Approvals (25)` tab read-only (`09-manager-approvals-readonly.png`), and triggered a `deterministic_fixture` evaluation run on `/analytics`.
- **Screenshots (`docs/development/screenshots/`)**: Captured 9 desktop & mobile screenshots (`01-login-desktop.png` through `09-manager-approvals-readonly.png`).

### Blocked / Not Exercised Live & Reconciled Findings
- **Real S3 presigned byte upload**: Local backend runs with `DOCUMENT_STORE=memory`, so live `https://` S3 PUT + ingestion worker completion could not be exercised without AWS/LocalStack S3 wiring; covered in mocked tests (`documents/page.test.tsx`).
- **Live Manager `POST /approvals/{id}/decision` & `Approvals (25)` reconciliation**: Read-only API inspection (`GET /api/v1/approvals?limit=25` as `manager@acme.test`) confirmed **25 pending approval rows** already exist in the local database (for example, `cd7dadf4-1e96-403e-98e2-dbc2648810c4`, `action_type: escalate_risk`, `status: pending`), matching the `Approvals (25)` tab label in the Manager screenshots. An earlier draft of this handoff mistakenly stated 0 pending approvals because the Approvals tab had not been opened/queried. Live `POST /approvals/{id}/decision` was intentionally **not** executed against existing local DB approvals so existing demo state remains unmodified; approval selection, failure recovery, and submission safety are verified in `risks/page.test.tsx`.
- **Live Bedrock inference / Cognito Hosted UI**: Not exercised (no AWS/paid inference authorized).

---

## 4. Next Commands

```bash
git status
git diff --check
```
