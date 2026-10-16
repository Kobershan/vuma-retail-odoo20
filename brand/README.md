# Brand Assets

Drop your Vuma Retail logo files in this folder using the names below.

OpenCode cannot accept binary uploads through the chat, so save the files here on disk
(or paste a base64 data URL) and tell me to apply them.

Required (recommended formats):

- vuma_logo.svg            -> web client logo (light mode)
- vuma_logo_dark.svg       -> web client logo (dark mode)
- logo.png                 -> fallback/company logo (PNG, ~200x60, transparent)
- logo2.png                -> database manager page (PNG, ~240x80)
- logo_inverse_white_206px.png  -> optional inverted logo (PNG)
- favicon.ico              -> browser tab icon
- odoo-icon-192x192.png    -> PWA icon (192x192 PNG)
- odoo-icon-512x512.png    -> PWA icon (512x512 PNG)
- odoo-icon-ios.png        -> iOS home icon (180x180 PNG)
- odoo-icon.svg            -> SVG app icon

Once placed, I will copy them over Odoo's static image assets and update the DB.

Website logo (shown on the site) can also be changed in Odoo:
  Settings/Website -> Configuration -> Website -> upload logo
or I can load the file directly into `ir.attachment` via SQL.
