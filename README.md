# My Lists

A fast, offline-first manager for large collections of WhatsApp phone numbers.

Open it, hit **+**, and add numbers one by one or paste thousands at once. A number can
exist **only once across the whole app** — every add, bulk import and move is checked
against every list, not just the current one.

## What it does

- **Lists** — create unlimited lists, rename, delete (with confirmation), live number count.
- **Add one number** — `0812 3456 7890`, `081234567890`, `+62 812-3456-7890` are all stored
  as the same number: `6281234567890`. Duplicates are rejected everywhere; landlines and
  junk (`12345`, `abcdef`) are refused.
- **Bulk add** — paste or upload a `.txt`/`.csv` with thousands of lines. Numbers are
  extracted, cleaned, deduplicated against every list, and invalid lines are skipped.
  A summary reports **Added / Duplicates skipped / Invalid numbers**.
- **Move** — send a number to another list without ever creating a duplicate.
- **Search** — type any format (`0813`, `+62 813-999`, `99887`) and it matches instantly.
- **Export** — current list or all lists, as TXT, CSV (opens in Excel), Excel `.xls`, or a
  full JSON backup.
- **Cloud (optional)** — mirror everything to Firebase Firestore (project `cool-b788e`).
  Requires Anonymous sign-in + the rules below.

## Design

Single file, no build step, no dependencies, no CDN. Storage is **IndexedDB**, used as a
real database: the canonical number is the primary key, so global uniqueness is enforced by
the database itself, not by JavaScript. Lists are browsed through a compound index
(`listId + number`) with 100 rows per page, so the browser never holds more than one page
in the DOM — 100,000 numbers stay fast. Counting uses native index counts (a full count of
19,000 numbers takes ~2 ms). Prefix search is an index range query (≈6–9 ms at 19,000
numbers); a mid-number search falls back to a bounded cursor walk (~400 ms).

## Firebase rules (Firestore → Rules)

```
rules_version = '2';
service cloud.firestore {
  match /databases/{db}/documents {

    function signedIn() { return request.auth != null; }
    function validList() {
      return request.resource.data.name is string
        && request.resource.data.name.size() > 0
        && request.resource.data.name.size() <= 80;
    }

    match /lists/{listId} {
      allow read:  if signedIn();
      allow create, update: if signedIn() && validList();
      allow delete: if signedIn();
    }

    // doc id = the canonical number → a number can only exist once in the database
    match /numbers/{number} {
      allow read:  if signedIn();
      allow create, update: if signedIn()
        && number.matches('^[0-9]{8,15}$')
        && request.resource.data.listId is string
        && exists(/databases/$(db)/documents/lists/$(request.resource.data.listId));
      allow delete: if signedIn();
    }
  }
}
```
