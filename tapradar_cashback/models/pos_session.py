from odoo import models


class PosSession(models.Model):
    _inherit = "pos.session"

    def _loader_params_res_partner(self):
        params = super()._loader_params_res_partner()
        params["search_params"]["fields"] += ["tapradar_link_url", "tapradar_linked", "tapradar_card"]
        return params
