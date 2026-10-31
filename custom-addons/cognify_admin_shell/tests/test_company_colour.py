# -*- coding: utf-8 -*-
"""Tests for the per-company Vuma shell colour."""

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "cognify_admin_shell")
class TestCompanyColour(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

    def test_colour_is_stored(self):
        self.company.write({"vuma_background_color": "#2563EB"})
        self.assertEqual(self.company.vuma_background_color, "#2563EB")

    def test_invalid_colour_is_rejected(self):
        for value in ("blue", "#12345", "#2563EB; background: red", "2563EB"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    self.company.write({"vuma_background_color": value})

    def test_empty_colour_is_allowed(self):
        self.company.write({"vuma_background_color": False})
        self.assertFalse(self.company.vuma_background_color)

    def test_company_form_exposes_colour_picker(self):
        view = self.env.ref("cognify_admin_shell.view_company_form_vuma_colour")
        self.assertIn('name="vuma_background_color"', view.arch_db)
        self.assertIn('widget="color"', view.arch_db)

    def test_session_info_includes_company_colour(self):
        self.company.write({"vuma_background_color": "#EA580C"})
        session_info = {
            "user_companies": {
                "allowed_companies": {
                    self.company.id: {
                        "id": self.company.id,
                        "name": self.company.name,
                    },
                },
            },
        }
        result = self.env["ir.http"]._vuma_add_company_colours(session_info)
        company_info = result["user_companies"]["allowed_companies"][self.company.id]
        self.assertEqual(company_info["vuma_background_color"], "#EA580C")

    def test_session_info_without_companies_is_safe(self):
        session_info = {"uid": 1}
        result = self.env["ir.http"]._vuma_add_company_colours(session_info)
        self.assertEqual(result, {"uid": 1})

    def test_colour_is_searchable_in_settings(self):
        view = self.env.ref(
            "cognify_admin_shell.res_config_settings_view_form_vuma_colour")
        self.assertIn('name="vuma_background_color"', view.arch_db)
        self.assertIn('string="Background Colour"', view.arch_db)

        settings = self.env["res.config.settings"].create({
            "company_id": self.company.id,
        })
        settings.vuma_background_color = "#112233"
        self.assertEqual(self.company.vuma_background_color, "#112233")

    def test_colour_menu_and_editable_list_exist(self):
        menu = self.env.ref("cognify_admin_shell.menu_vuma_company_colours")
        self.assertIn("colour", menu.name.lower())
        self.assertIn("background", menu.name.lower())
        self.assertEqual(
            menu.action,
            self.env.ref("cognify_admin_shell.action_vuma_company_colours"))
        view = self.env.ref("cognify_admin_shell.view_company_colour_list")
        self.assertIn('name="vuma_background_color"', view.arch_db)
        self.assertIn('widget="color"', view.arch_db)
