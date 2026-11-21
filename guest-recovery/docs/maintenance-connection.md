# Connect Guest Recovery to Maintenance

1. In Maintenance, match each Hospitable property ID to its home and create a connection token for **Guest Recovery** using **Connect your apps**.
2. Set `MAINTENANCE_ORIGIN` to the Maintenance app's HTTPS origin and `MAINTENANCE_CONNECTION_TOKEN` to the generated token in Guest Recovery's server environment. Restart Guest Recovery after updating the environment. The token currently expires after 24 hours. Replace it when it expires.
3. In the Guest Recovery inbox, open a current maintenance issue, enter a repair summary, and select **Send to Maintenance**. Include the fault and its location in the home. Omit names, contact details, and access codes.
4. Use **Refresh repair status** in the same issue to retrieve updates from Maintenance.

Real guest imports require a Maintenance workspace with real accounts and linked homes. Demo mode rejects those imports. Safety reports remain in the safety workflow. Guest Recovery sends the reviewed summary and routing identifiers; the full conversation stays in Guest Recovery.
