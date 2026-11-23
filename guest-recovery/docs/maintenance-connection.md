# Connect Guest Recovery to Maintenance

1. In Maintenance, match each Hospitable property ID to its home and create a connection token for **Guest Recovery** using **Connect your apps**.
2. In Guest Recovery, open **Settings → Maintenance connection** and enter the Maintenance API origin, dashboard origin, and connection token. The token currently expires after 24 hours. Replace it when it expires.
3. In the Guest Recovery inbox, open a current maintenance issue, enter a repair summary, and select **Open in maintenance**. Include the fault and its location in the home. Omit names, contact details, and access codes.
4. Use **Refresh repair status** in the same issue to retrieve updates from Maintenance.

Real guest imports require a Maintenance workspace with real accounts and linked homes. Demo mode rejects those imports. Safety reports remain in the safety workflow. Guest Recovery sends the reviewed summary and routing identifiers; the full conversation stays in Guest Recovery.

Incoming guest messages labeled **Maintenance** are routed automatically for current stays once the workspace and property are linked. The first report opens an intake. The inbox sends Maintenance’s existing room, onset, access, and entry-safety questions one at a time when Hospitable messaging is configured. It adds the guest’s direct answers to the same repair. Maintenance moves the repair to manager review when intake is complete or urgent, and the inbox refreshes its status. Photos and separate contact details can be handled during manager review. The automation forwards fault details rather than the full conversation. Safety reports use the safety workflow. Individually linked historical repairs and follow-ups after closure can still be reviewed in the inbox.
