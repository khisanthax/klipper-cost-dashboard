# Application modes

Open **Settings → Other → Application Mode**, choose a mode, and save. The setting applies to every browser connected to this installation. An existing installation opens in Advanced unless someone has saved a preference.

- **Personal** shows printer status, print history, Projects, basic costs, profiles, and everyday settings. Reports, Recalculate, advanced table controls, and exports belong to Advanced and Business.
- **Advanced** includes Projects, Reports, charts, Recalculate, CSV export, Pause Accounting, and the additional table settings.
- **Business** includes the Advanced workspace and exposes [Orders and profitability](business-orders.md) on SQL-only installations.

Mode switching changes presentation. History, project assignments, order records, stored prices, profile settings, and printer identities are preserved. Saved links to advanced controls still work. An Orders link opened in Personal or Advanced shows a mode prompt. Switch back to Business to open those orders again.

The setting does not change access permissions. Configure access to the KCD service separately if you need to control who can view or edit data.

SQL-only installations store the preference in the existing `user_settings` table, under `application_mode`. CSV and dual compatibility installations store it in `data/application_mode.json`. KCD includes this preference in the standard data backup. A missing or unsupported mode value selects Advanced.
