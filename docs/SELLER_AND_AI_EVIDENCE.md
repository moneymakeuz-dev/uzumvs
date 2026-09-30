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

Sources: [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) (page last updated 2026-09-24) and [Gemini API Additional Terms](https://ai.google.dev/gemini-api/terms) (effective 2026-03-23; page last updated 2026-04-28). Checked 2026-09-30.

- Gemini 2.5 Flash paid standard rates shown are USD 0.30 per 1M text/image/video input tokens and USD 2.50 per 1M output tokens, including thinking tokens. Free quota is shown as free of charge. Prices can change; recheck before release.
- For unpaid services, Google says submitted content and responses may be used to provide, improve and develop products and machine-learning technologies, and human reviewers may process them. The terms advise not submitting sensitive, confidential or personal information to unpaid services.
- For paid Gemini API services (a Cloud project with active billing), Google says prompts, associated instructions/files/images and responses are not used to improve products. It still retains limited data for safety/security and legal purposes; storage may be transiently cached in countries where Google or agents operate.
- A local budget value and API key do not prove that the key's Cloud project has active billing or that calls use paid quota. Confirm the billing tier and data-use basis before uploading seller/customer images. No live call or seller content was sent during this review.

The project's internal estimate is not a Google invoice. Owner must approve the daily spend ceiling, terms/data-use choice and the sample-data consent before T04/T34/T66 can close.