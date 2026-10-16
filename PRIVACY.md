# Privacy and responsible use

Effective 16 October 2026. This notice applies to the EzyNotify Telegram bot and website monitoring service.

## Your responsibility when monitoring websites

You select the websites, keywords and monitoring settings. Before adding a website, you are responsible for checking its robots.txt, terms of service, applicable copyright, licensing and privacy requirements, and obtaining any permission required for your intended use. Recheck these requirements when they change. Public accessibility and a permissive robots.txt do not by themselves authorize every use of a website or its content.

Use this service only for lawful, authorized monitoring. Do not submit credentials, access tokens, private links or sensitive personal data. Do not use the service to circumvent access controls, CAPTCHA, paywalls or other restrictions. The service does not fetch or enforce robots.txt. You must independently check it and obtain any required permission before monitoring. The Robots Exclusion Protocol itself is not access authorization: see [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html).

## Third-party websites and service limitations

The operator does not operate, endorse or control third-party websites or their content. You remain responsible for your monitoring choices and subsequent use of information. Website access can fail, alerts can be delayed or missed, and AI summaries can be incomplete or incorrect. Verify important information using the original source. The service is provided as available without a guarantee of uninterrupted operation, accuracy or suitability for a particular purpose.

To the extent permitted by applicable law, the operator disclaims warranties and liability for third-party content, your monitoring choices and resulting losses. Nothing in this notice excludes mandatory rights, remedies or responsibilities that applicable law does not permit to be excluded. This notice does not authorize unlawful monitoring or transfer the operator's own statutory obligations to users.

## Information processed

To operate, secure and support the service, we process your Telegram ID, available Telegram profile details, language, interaction records, timezone, URLs, keywords, monitor settings, saved page snapshots, check history and notification delivery records. Website requests disclose technical connection information to the target website.

Telegram, hosting, automation and AI service providers process information necessary to provide these features. Page text, excerpts or screenshots may be sent for summaries or access-block classification. Do not monitor pages containing sensitive information. Provider processing may occur outside your country.

Every failed website check sends the operator a diagnostic alert containing the website hostname, monitor ID and type, failure status and reason, time and consecutive failure count. These alerts exclude page content, URL paths and queries, and user profile details. Internal request retries are not separate completed checks. Telegram retains delivered messages according to its own policies and account controls.

## Retention and controls

The scheduled cleanup normally removes check history after 90 days, completed or failed notification delivery records after 30 days, and unused fetch cache after 7 days. Drafts expire after 30 minutes and are cleaned up daily. Telegram update deduplication records are cleaned up after 7 days. Pending delivery records remain while retrying. Cleanup depends on the scheduler operating successfully.

Profiles, preferences and saved monitors remain until removed or reviewed for deletion. You can remove monitors through My monitors. Deletion of a monitor does not automatically erase all historical, profile or delivery records. Backups and records required by law or needed to resolve a dispute may persist longer.

Open `/privacy` in the bot and select **Privacy request** to request access, correction, deletion or raise another privacy concern. Requests are saved in Supabase for manual operator review. Submitting a request does not immediately erase information. The operator may contact you through the bot for clarification or identity verification and must handle requests under applicable law. The operator should regularly review the `privacy_requests` table and record progress there.

Access to operational tables is restricted to the service role. Security controls reduce risk but cannot guarantee absolute security. Material policy changes will be reflected in the bot notice and this document. Applicable data protection and consumer rights remain unaffected, including where relevant the [Nigeria Data Protection Act](https://www.ndpc.gov.ng/ndp-act-2023/) and [Federal Competition and Consumer Protection Act](https://fccpc.gov.ng/wp-content/uploads/2022/07/FCCPA-2018.pdf).
