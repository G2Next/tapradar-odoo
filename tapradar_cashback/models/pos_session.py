from odoo import models


class PosSession(models.Model):
    _inherit = "pos.session"

    def _loader_params_res_partner(self):
        params = super()._loader_params_res_partner()
        params["search_params"]["fields"].append("tapradar_link_url")
        return params
