# Seller and AI evidence

Checked: 2026-09-30. Public-source review only; it does not replace checking the authenticated seller cabinet, the seller's chosen pilot categories, the actual Google billing project, or the account's applicable contract.

## Uzum Market product-card rules

Primary source: [Uzum Market seller manual, section 5: product creation](https://seller.uzum.uz/manual/5.product-creation/).

- Sections 5.3, 5.6, 5.8, 5.9 and 5.10 require Uzbek (Latin) and Russian (Cyrillic) for titles, descriptions, short descriptions, characteristics and additional fields.
- Section 5.3 says a title should have at least three words, start with a capital letter, avoid abbreviations, follow product type + brand + model + key characteristic, and comply with section 5.1. Section 5.1 disallows personal contacts, external links/services, irrelevant search tagging, subjective review/rating/popularity claims, unsupported medical claims, profanity, title emoji and other listed violations. The complete stop-word list is intentionally not duplicated here; use the source section itself.
- Section 5.6 requires an accurate description of actual product properties. It also says products without a limited shelf life need warranty information; absent a seller-set term, the manual states a six-month default. Section 5.8 says short description is 1-2 sentences.
- Section 5.2 puts category selection responsibility on the seller and says an incorrect category can cause delisting. Sections 5.3, 5.6 and 5.9 include category-specific content/attribute requirements, including separate requirements for animals and other restricted categories.
- The public manual did not state maximum character counts for title/description in the reviewed sections. Current app limits (200 / 10,000 / 1,000 characters) remain internal implementation limits, not verified Uzum limits. The seller cabinet may impose additional limits.
- The public manual describes creating product cards in the seller cabinet. Section 5.14 describes downloading and uploading a spreadsheet template for bulk price updates, not an import template for creating complete product cards. Availability of category-specific product-creation import templates and their use terms remains unverified until checked in a seller account.

These rules do not certify that generated text passes moderation. The chosen pilot categories still need seller confirmation, and the app currently has no dedicated warranty field; decide with the seller how to handle warranty facts before claiming category readiness.

## Gemini API pricing and data use

Sources: [Gemini 3.8 Flash model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash) (last updated 2026-09-02), [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) (last updated 2026-09-24), and [Gemini API Additional Terms](https://ai.google.dev/gemini-api/terms) (effective 2026-03-23; page last updated 2026-04-28). Checked 2026-09-30.

- The configured model is `gemini-3.8-flash`. Google's model page lists text, image, video, audio and PDF input, text output, and structured outputs support.
- Gemini 3.8 Flash paid standard rates shown are USD 0.75 per 1M input tokens and USD 3.75 per 1M output tokens, including thinking tokens, through 2026-12-31; the page lists USD 1.50 / USD 7.50 from 2027-01-01. Free quota is shown as free of charge. Prices can change; recheck before release.
- Gemini 3.8 Flash defaults to medium thinking; the official thinking guide says thinking tokens count toward `max_output_tokens`. The provider now requests the documented low thinking level for this model so reasoning is less likely to consume the response budget.
- On 2026-09-30, the configured Gemini 2.5 Flash model returned HTTP 404: unavailable to new users. The model catalog listed it, but this account could not count tokens with it. No successful 2.5 generation was confirmed; settings were changed to the documented 3.8 model and its rates.
- On 2026-09-30, Gemini 3.8 model listing and token counting succeeded. A minimal text request at 16 output tokens returned `MAX_TOKENS` with empty text; the full card schema returned HTTP 400 `INVALID_ARGUMENT`, and a later minimal structured-output attempt returned HTTP 503 `UNAVAILABLE`. A one-sample synthetic evaluator attempt had no token-usage metadata and was not accepted. These requests used only synthetic content; no seller/customer data or seller account credentials were sent. AI quality and a successful structured image generation remain unverified.
- For unpaid services, Google says submitted content and responses may be used to provide, improve and develop products and machine-learning technologies, and human reviewers may process them. The terms advise not submitting sensitive, confidential or personal information to unpaid services.
- For paid Gemini API services (a Cloud project with active billing), Google says prompts, associated instructions/files/images and responses are not used to improve products. It still retains limited data for safety/security and legal purposes; storage may be transiently cached in countries where Google or agents operate.
- A local budget value and API key do not prove that the key's Cloud project has active billing or that calls use paid quota. Confirm the billing tier and data-use basis before uploading seller/customer images. No live call or seller content was sent during this review.

The project's internal estimate is not a Google invoice. Owner must approve the daily spend ceiling, terms/data-use choice and the sample-data consent before T04/T34/T66 can close.