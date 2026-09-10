You are working on a production-grade standalone Python application named **`qwen-image-archive-cli`**.

I will upload the **complete current source code as a ZIP**. Carefully inspect the entire uploaded source before making changes. **The uploaded source is the authoritative current state. Do not reconstruct files from assumptions, prior examples, or memory.**

## PURPOSE

`qwen-image-archive-cli` is a completely independent application for analyzing and organizing a very large image archive containing **more than 1,000,000 images**.

It runs a **local Qwen multimodal LLM directly from Python** and does not depend on:

* `gemini-vision-cli`
* Google Gemini
* Chrome
* Playwright
* ComfyUI
* cloud inference APIs

The application is intended primarily for Windows and NVIDIA CUDA systems.

My current machine is approximately:

* Intel Core i9 13th Gen
* NVIDIA GPU with 12 GB VRAM
* Windows
* Python
* Local Qwen model

The primary model is:

`Qwen/Qwen3.5-4B`

or an equivalent locally cached Qwen3.5-4B model.

The application should be optimized aggressively but safely for this fixed workload.

---

# TASKS

The application has exactly two primary tasks:

1. `prompts`
2. `organize`

Both tasks should expose the **same general CLI parameter contract** and matching PowerShell wrapper parameters wherever technically applicable.

PowerShell wrappers should exist for both tasks.

Typical usage should look similar to:

`prompts.ps1 -InputPath "." -Recurse`

and:

`organize.ps1 -InputPath "." -Recurse`

---

# CORE ENGINEERING PRINCIPLES

Treat this as an enterprise application maintained by a senior engineering team.

Always:

* Preserve currently working behavior unless a requested change explicitly requires modification.
* Prefer modular, reusable, SOLID/OOP architecture.
* Shared functionality must live in shared services rather than being duplicated between tasks.
* Use bounded retries and bounded timeouts.
* Never create infinite loops or waits.
* A failure processing one image should normally be logged and skipped rather than terminating the complete batch.
* Preserve Unicode throughout filenames, metadata, prompts, JSON, logs, PowerShell, and filesystem operations.
* Never unnecessarily resize, re-encode, recompress, or alter source image pixels.
* Preserve filesystem timestamps where required.
* Never overwrite a different existing file.
* Use collision-safe naming.
* Folder parameters must also support a single image file.
* Do not silently destroy existing metadata.
* Do not silently replace existing SEO tags.
* Validate AI output before writing metadata or moving files.
* Prefer deterministic behavior over heuristic behavior when metadata is already available.

Before modifying the project:

1. Inspect all directly related files.
2. Trace the complete execution path.
3. Keep Python CLI, services, configuration, and PowerShell wrappers synchronized.
4. Run syntax/static validation.
5. Run meaningful automated/edge-case tests.
6. Do not claim a fix is complete unless it was actually implemented and validated.

For source-code deliverables, provide a ZIP containing only the files requested by me, preserving directory structure.

---

# LOCAL QWEN INFERENCE

Qwen should be loaded directly inside Python using an appropriate current Hugging Face/Transformers multimodal implementation.

The model should normally be loaded **once per process** and reused across all images.

The application should optimize inference for this fixed workload.

Preferred optimizations include, where safe and supported:

* CUDA execution
* BF16 when appropriate
* quantized fallback where useful
* inference-only mode
* deterministic/low-temperature generation
* thinking disabled for structured classification work
* bounded `max_new_tokens`
* model-resident execution
* generation KV caching
* reusable fixed prompt templates
* tokenizer/template reuse
* persistent content-addressed inference-result cache
* text-only inference when the existing Comments metadata provides sufficient context
* image inference only when required
* sensible image-resolution/token budgeting to reduce VRAM pressure

Do **not** implement unsafe manual multimodal KV-cache reuse across unrelated images unless correctness is proven.

Correctness is more important than marginal speed improvements.

The program should be able to use a locally downloaded/cached model so normal execution does not require internet access.

---

# METADATA BACKEND

Use **ExifTool** as the authoritative metadata backend where appropriate.

Metadata handling must preserve existing metadata unless explicitly requested otherwise.

Important Windows metadata fields include:

* `Title`
* `Subject`
* `XPSubject`
* `Comments`
* `XPComment`
* `Tags`
* keywords/SEO tags
* camera/artist EXIF fields where configured

Preserve filesystem timestamps after metadata operations when required.

Existing SEO tags must be merged, not replaced.

Default camera/artist metadata should only populate missing values and must not overwrite meaningful existing camera metadata.

---

# PAINTING / IMAGE ANALYSIS JSON

The application stores analysis JSON in Windows `Subject` / `XPSubject`.

Current metadata may contain fields such as:

```json
{
  "schemaVersion": 4,
  "title": "...",
  "description": "...",
  "shade": "...",
  "searchTerms": [],
  "categories": [],
  "folderClassification": {
    "folders": [],
    "confidence": 0.95,
    "subject": "...",
    "needsReview": false
  },
  "prompt": "..."
}
```

The application must preserve unknown/additional JSON properties whenever updating only one portion of Subject metadata.

Do not unnecessarily regenerate already valid metadata.

---

# folderClassification

`folderClassification` is an **independent archival classification system**.

It is NOT restricted to any Fizdi/category taxonomy.

This application has nothing to do with Fizdi marketplace category selection.

The archive contains more than one million images, so classifications must be detailed enough to distribute files meaningfully.

A folder classification contains:

```json
{
  "folders": [
    "Level 1",
    "Level 2",
    "Level 3",
    "Level 4",
    "Level 5",
    "Level 6"
  ],
  "confidence": 0.95,
  "subject": "short primary subject",
  "needsReview": false
}
```

## Folder hierarchy rules

The folder level range must be read from `config/settings.json`. The default production configuration is **6 to 9 levels** (`application.folderClassification.minLevels=6`, `maxLevels=9`).

Classify primarily by **what the image depicts**, not its medium.

Choose the strongest archival identity: the subject somebody would naturally use to find the image.

Structure broad → specific.

Each folder must represent a stable reusable category suitable for many future images.

Avoid temporary visual descriptions and one-off categories.

Geography, religion, culture, activity, historical period, environment, etc. may be used when materially useful.

Named people, landmarks, species, deities, places, characters, historical events, etc. may be used when identification is reasonably certain.

Never invent uncertain facts.

Do not use synonyms or duplicate levels.

Folder names must:

* use Title Case
* be concise noun phrases
* contain no filenames
* contain no numbering
* contain no `/`
* contain no `\`
* be safe Windows directory names

Do not use vague or medium-based archive folders such as:

* Other
* Miscellaneous
* General
* Images
* Pictures
* Artwork
* Art
* Fine Art
* Painting
* Paintings
* Photography
* Photograph
* Photographs
* Drawing
* Drawings
* Digital Art
* Uncategorized


The prohibited/vague-folder list is a **quality rule, not a job-failure rule** by default. If Qwen returns an otherwise structurally valid classification containing one or more prohibited suggestions (including two or three such levels), Artifex must log a warning and continue when `failOnProhibitedFolder=false`.

Likewise, low confidence and `needsReview=true` are warning-only by default when `failOnLowConfidence=false` and `failOnNeedsReview=false`. These policies are settings-driven and must not be hardcoded.

The conceptual question the model should answer is:

> If this archive contained thousands of similar images, where should all of them consistently live?

Confidence rules:

* `0.90–1.00` = highly certain
* `0.75–0.89` = reasonably certain
* `0.50–0.74` = uncertain
* below `0.50` = should be reviewed

Set `needsReview=true` when:

* confidence is below 0.75
* an important named entity is uncertain
* multiple subjects strongly compete
* the image is too ambiguous for reliable classification

---

# PROMPTS TASK

The `prompts` task performs local multimodal analysis and writes image metadata.

It should reuse valid cached Subject/XPSubject metadata whenever appropriate rather than calling Qwen unnecessarily.

The task may generate/update data such as:

* title
* description
* shade
* SEO/search terms
* folderClassification
* transformation prompt
* Subject JSON
* Comments/XPComment
* Title
* Tags

Existing valid metadata should be preserved where possible.

Unsupported but non-empty shade descriptions may normalize to an appropriate canonical fallback such as `Multi-Color` rather than causing long retries.

AI responses must be parsed and validated robustly.

Malformed model output should use bounded correction/retry behavior.

Never wait indefinitely for a response that has clearly completed but is invalid.

---

# ORGANIZE TASK

The `organize` task should be primarily deterministic.

Processing order:

1. Read `Subject` / `XPSubject`.
2. If it contains valid JSON with a valid `folderClassification`, use that folder path immediately.
3. Do **not** call Qwen in that case.
4. If `folderClassification` is missing or invalid:

   * if valid `Comments` / `XPComment` exists, prefer text-only Qwen classification from the Comments text;
   * otherwise use the original image for local multimodal classification.
5. Validate the returned `folderClassification`.
6. Merge only `folderClassification` into the existing Subject JSON.
7. Preserve all other Subject JSON fields unchanged.
8. Write the merged JSON back safely.
9. Organize/move the file according to the configured folder path (default 6–9 levels).
10. If classification/move ultimately fails, append the normal image tag:
    `MoveFailed`

Never remove existing tags when adding `MoveFailed`.

If the file is later successfully processed, follow the current source behavior regarding whether `MoveFailed` is retained or removed; inspect the code before changing this behavior.

The Organize task must not require browser automation or cloud AI.

---

# ORGANIZE FILESYSTEM BEHAVIOR

Organize must:

* support a single file
* support a directory
* support recursive scanning
* preserve source filesystem timestamps
* safely create destination directories
* never overwrite a different existing file
* use collision-safe behavior
* support sequential bucket folders when destinations become large

Typical bucket structure:

`00001 - 00250`

`00251 - 00500`

`00501 - 00750`

etc.

Bucket size should be configurable.

The classification hierarchy itself should remain independent of bucket folders.

---

# INFERENCE CACHE

Because the classification prompt is fixed and the archive is extremely large, inference caching is important.

The application should maintain a persistent content-addressed cache.

The cache key should safely account for things such as:

* task/type of inference
* model identity/version
* prompt/template version
* input text hash or image content hash
* relevant inference settings

Never reuse stale cached results after changing materially relevant prompt/model configuration.

Cached AI responses must still pass current validation before reuse.

---

# POWERSHELL WRAPPERS

Both tasks must have professional PowerShell wrappers.

At minimum:

* `prompts.ps1`
* `organize.ps1`

The wrappers should expose the same parameter set wherever applicable.

Canonical input parameter should be:

`-InputPath`

Support common options such as:

* recursion
* model location/name
* device
* precision
* caching
* metadata controls
* confidence threshold
* bucket size
* logging
* state/cache paths
* preview/dry-run behavior

PowerShell must preserve Unicode correctly.

`-DryRun` must print the complete Python command that would be executed and must not start Python.

The application-level preview mode should perform a genuine no-write preview and preferably avoid loading the large model if inference is unnecessary for the preview.

Do not allow PowerShell pipeline output to accidentally contaminate numeric exit codes or execution summaries.

---

# CONFIGURATION

Prefer structured JSON configuration files rather than scattering settings across task code.

Configuration should clearly separate:

* model/inference settings
* metadata settings
* prompt versions
* folder classification policy
* cache behavior
* organize filesystem behavior
* logging/timeouts/retries

Explicit command-line parameters should normally override configuration defaults.

Configuration changes that affect AI output should participate in cache versioning/invalidation.

---

# LOGGING

Logging should clearly show:

* input file
* whether Subject cache was reused
* whether Comments or image inference was used
* whether inference cache was hit
* model/inference failures
* parsed folder classification
* confidence
* metadata writes
* destination path
* collision handling
* MoveFailed tagging
* per-file skip/failure
* batch summary

Avoid excessive noisy logs during normal execution.

Never expose chain-of-thought or hidden reasoning from the model.

---

# ERROR HANDLING

Use bounded retries.

One bad image should not abort the whole archive batch.

Errors should identify:

* filename
* operation
* reason
* whether the file was skipped/retried/tagged

GPU OOM should be handled gracefully where practical.

If BF16 cannot fit reliably, support an appropriate configurable quantized mode rather than silently falling back to extremely slow CPU execution unless CPU fallback was explicitly enabled.

---

# TESTING

Before delivering any change:

Run:

* Python syntax/compile validation
* import validation
* JSON config parsing
* unit tests
* folderClassification 6–9 level validation tests
* non-fatal prohibited-folder quality-policy tests
* Subject JSON merge tests
* metadata preservation tests where practical
* cache tests
* collision-safe move tests
* single-file tests
* recursive discovery tests
* PowerShell parameter parity checks
* DryRun checks

For high-risk changes, add targeted regression tests.

Do not claim validation that was not actually performed.

---

# SOURCE-CODE DELIVERY

When I request code modifications:

* inspect the uploaded ZIP first
* modify the actual source
* validate the result
* provide only the files requested by me
* preserve original directory structure
* package source-code changes in a ZIP

When I ask for a complete release, include the entire standalone application.

Do not provide unnecessary long descriptions with code deliveries.

A short Git commit comment is normally sufficient unless I specifically request details.

---

# CURRENT PRIORITIES

The most important characteristics of this application are:

1. **Local-only Qwen inference**
2. **No unnecessary inference when metadata already exists**
3. **High-quality independent 6–9-level folder classification**
4. **Fast repeated fixed-prompt inference**
5. **Reliable metadata preservation**
6. **Safe organization of a million-image archive**
7. **No accidental image modification**
8. **No unbounded waits**
9. **Strong caching**
10. **Production-quality Windows + NVIDIA behavior**

Treat the uploaded source code as authoritative and continue development from that exact state.
