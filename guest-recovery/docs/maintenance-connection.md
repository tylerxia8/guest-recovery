# Connect Guest Operations to Maintenance

1. In Maintenance, match each Hospitable property ID to its home and create a connection token for **Guest Operations** using **Connect your apps**.
2. In Guest Operations, open **Settings → Maintenance connection** and enter the Maintenance API origin, dashboard origin, and connection token. The token currently expires after 24 hours. Replace it when it expires.
3. In the Guest Operations inbox, open a current maintenance issue, enter a repair summary, and select **Open in maintenance**. Include the fault and its location in the home. Omit names, contact details, and access codes.
4. Use **Refresh repair status** in the same issue to retrieve updates from Maintenance.

Real guest imports require a Maintenance workspace with real accounts and linked homes. Demo mode rejects those imports. Safety reports remain in the safety workflow. Guest Operations sends the reviewed summary and routing identifiers; the full conversation stays in Guest Operations.
