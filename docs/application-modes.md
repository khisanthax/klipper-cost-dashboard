# Application modes

Open **Settings → Other → Application Mode**, choose a mode, and save. The setting applies to every browser connected to this installation. An existing installation opens in Advanced unless someone has saved a preference.

- **Personal** shows printer status, print history, basic costs, profiles, and everyday settings. The ordinary menus and dashboard omit Projects, Reports, Recalculate, and advanced table and export controls.
- **Advanced** includes Projects, Reports, charts, Recalculate, CSV export, Pause Accounting, and the additional table settings.
- **Business** currently includes the Advanced workspace. Order and profitability controls will be added with the business milestone.

Mode switching changes presentation. History, project assignments, stored prices, profile settings, and printer identities are preserved. Previously saved links still work. Switch back to Advanced or Business to restore the advanced controls in the menus.

The setting does not change access permissions. Configure access to the KCD service separately if you need to control who can view or edit data.

SQL-only installations store the preference in the existing `user_settings` table, under `application_mode`. CSV and dual compatibility installations store it in `data/application_mode.json`. KCD includes this preference in the standard data backup. A missing or unsupported mode value selects Advanced.
