# Website localization

Swedish is the default and currently only website locale. `DEFAULT_LOCALE=sv_SE`
is validated at startup. The HTML language is `sv`, the response language is
`sv-SE`, and Accept-Language does not switch the interface. No database migration
or user-language preference was added.

## Structure

- `web/i18n.py` loads standard GNU gettext catalogs, provides the Python `_`
  helper and Babel date/time/weekday formatting. The request locale is scoped
  with a ContextVar and restored after each request, including failures.
- The Jinja environment installs the same catalog using its standard i18n
  extension. Templates use English msgids, named interpolation and `ngettext`
  for counts. Values still pass through Jinja HTML escaping.
- `web/presentation.py` maps stable outcome, authentication, invitation, worker
  and audit values to marked English strings. Fixed service error/reason strings
  are allowlisted at the web boundary; unknown details get a safe Swedish message.
  Pydantic metadata is converted to field-specific guidance rather than exposed.
- `babel.cfg` extracts website Python and Jinja strings. `gettext_noop` / `N_`
  mark strings that are translated later by presentation helpers. Domain services
  have no dependency on website localization.
- `web/translations/messages.pot` is the source template;
  `sv_SE/LC_MESSAGES/messages.po` is the editable Swedish catalog and `messages.mo`
  is the compiled runtime artifact. Both catalogs and the copy-button JavaScript
  are included in setuptools package data and Docker images.

Use `make i18n-update`, edit the `.po`, then `make i18n-compile`. Extraction alone
is available as `make i18n-extract`. Review placeholders and plural forms before
compiling. Tests check every catalog entry is translated, has no fuzzy flag, and
agrees with the compiled catalog. Restart after catalog changes because runtime
translations are cached.

For another locale, initialize its catalog with `pybabel init`, translate and
compile it, enable the locale in Settings, and add the desired selection policy.
There is no need to replace template source strings. The current setting rejects
other locales instead of silently presenting a partially translated website.

## Stable values and intentional exceptions

The database continues to store values such as `BOOKED`, `DRY_RUN`, English audit
codes, English safe reason strings, integer weekdays and UTC timestamps. YAML,
legacy CLI output and internal logging remain unchanged. HTML form field names,
option values, URLs and ISO date/time input values are machine data; their
visible labels are localized.

Normal users see Swedish outcome and event labels. Administrators may additionally
see an audit identifier under **Kod**. Unknown historical reason strings receive
a generic Swedish explanation rather than arbitrary English or technical details.

Intentional unchanged text consists of the Campushallen Booker brand and other
proper nouns, user-entered account/rule names, product/facility names and other
external Campushallen data. The shared Swedish/English word “Status” and technical
identifiers such as API and ID remain appropriate in context. Europe/Stockholm is
shown as the configured timezone identifier. `DELETE` remains the fixed typed
confirmation word, with Swedish instructions; `DISCONNECT` is only a checkbox
value and is not displayed. No other English copy is intended for the website.

Dates use Swedish CLDR data through Babel and Stockholm daylight-saving rules.
Rule weekday labels use Babel's Monday-based indices, preserving existing
weekday matching. The frontend does not change UTC database storage.

## Verification

Localization tests cover login, onboarding, invite registration, accounts, rule
CRUD and validation, history labels and persisted values, authentication states,
admin pages, copy-link accessibility text, catalog/plural completeness, Swedish
dates with summer/winter offsets, and existing CSRF/authentication/ownership
boundaries. The browser smoke uses `sv-SE` contexts and Swedish accessible names;
it verifies copying an invitation and the complete mocked user flow, including
mobile overflow and screenshots of the main pages. All Campushallen traffic is
mocked; preview does not enable real bookings.
