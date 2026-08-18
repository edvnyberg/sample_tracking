========================
Test Sample Tracking
========================

Track test samples from when they arrive, through testing, and until they're sent back or scrapped away.

What This Module Does
======================


- **Organizing samples** - group related items from one customer together
- **Tracking each item** - follow where each item is, what state it's in
- **Serial numbers** - each physical item gets its own ID
- **Splitting and combining** - split items for different tests or combine them back later
- **Moving items around** - transfer between departments and assign to different people
- **Returning or disposing** - ship items back to customers or request approval to scrap them
- **Bulk actions** - handle many items at once instead of one by one
- **History and notes** - see who did what and when, with messages and activity logs

The Basics
==========

Sample
--------------------------
When a test sample is registered in a sale order and the customer sends you items to test, a **sample** is created to group them all together. Think of it as one shipment.

Sample Line
----------------------
Each item in the batch is a **line**. If a customer sends 5 items, you'd have 5 lines in one sample. Each line gets tracked separately and has:

- Its own ID
- How many units
- Serial numbers for each unit
- Who it's assigned to
- What state it's in

Unit Serials
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
If you're tracking 3 identical items, each one gets its own serial number so you can keep them straight. If you split them up later, the serial numbers stay with the right item.

The Item Journey
================

Each item goes through these stages:

1. **Incoming** - To be received or just arrived, quantity needs to be confirmed and inspected for any damages
2. **Received** - Quantity confirmed
3. **In Testing** - Being tested
4. **Tested** - Testing is done, ready for return or scrap

Then you can either return it to the customer or scrap it:
5. **Pending Return** - Getting ready to send back
6. **Returned** - Sent back to customer

Or instead of returning:

5. **Scrap Pending** - Request to throw it away submitted, waiting for approval
6. **Scrapped** - Approved to throw away

Splitting Items
===============

**Split** is useful when you need to test items different ways. For example:

- Customer sends 4 items in one package
- You want to test 2 under one method and 2 under another
- Use Split to separate them into two lines
- After testing both, you can use Merge to put them back together

**Explode** is when you split one item into individual lines.

Combining Items Back Together
==============================

**Merge** lets you combine split items back together. You pick which units from which line you want to bring together.

For example:
- You have Line A with 2 units and Line B with 2 units
- Use Merge to move 1 unit from Line B back to Line A
- Line A now has 3 units, Line B has 1 unit
- If a line ends up empty, it's automatically deleted

Or use **Bulk Merge** - mark multiple items in the list and click "Merge" to combine all the related ones at once.

Moving Items Between Locations
==============================

**Transfer** is how you move an item to a different department or location or assign it to a different person. You can transfer one item at a time, or use Bulk Transfer to move many at once.

Returning Items to Customers
=============================

When an item is ready to go back:

1. Click **Return**
2. Choose how it's going back:
   - **Shipment** - specify courier, address, tracking number
   - **Pickup** - Customer picks it up
3. Enter any shipping details
4. Logistics confirm when it ships
5. Item is marked as Returned

You can return one item or use Bulk Return for many at once.

Scrapping Items
===========================

If an item is to be scrapped, it needs approval from a manager. The workflow is:

1. Click **Scrap**
2. Select who needs to approve this
3. That person gets a notification
4. They can approve or reject

Who Can Do What
===============

There are three user levels:

**Regular User**
- Work with items assigned to them
- Move items around, request returns or scrap
- Can't approve scrap requests or access items from other people

**Logistics**
- Claim incoming items
- Inspect and confirm quantities
- Move items between locations
- Confirm shipments and pickups
- See all transfers and pending work

**Manager**
- Can do everything
- Approve scrap requests
- Configure settings
- Access all items


==========

**Author:** Edvin Nyberg  
**Company:** Intertek  
**License:** OPL-1



