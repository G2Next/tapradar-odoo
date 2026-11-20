from odoo import api, models

PROGRAM_FIELDS = {"name", "portal_point_name", "active"}
REWARD_FIELDS = {"description", "required_points", "active", "program_id"}


def _resend_programs(env, programs):
    """Send the loyalty cards of these programs again: what the TapRadar card shows changed."""
    programs = programs.exists().filtered(lambda p: p.program_type == "loyalty")
    if programs:
        cards = env["loyalty.card"].sudo().search([("program_id", "in", programs.ids), ("partner_id", "!=", False)])
        cards._tapradar_enqueue_loyalty()


class LoyaltyProgram(models.Model):
    _inherit = "loyalty.program"

    def write(self, vals):
        res = super().write(vals)
        if PROGRAM_FIELDS & set(vals):
            _resend_programs(self.env, self)
        return res


class LoyaltyReward(models.Model):
    _inherit = "loyalty.reward"

    @api.model_create_multi
    def create(self, vals_list):
        rewards = super().create(vals_list)
        _resend_programs(self.env, rewards.program_id)
        return rewards

    def write(self, vals):
        programs = self.program_id
        res = super().write(vals)
        if REWARD_FIELDS & set(vals):
            _resend_programs(self.env, programs | self.program_id)
        return res

    def unlink(self):
        programs = self.program_id
        res = super().unlink()
        _resend_programs(self.env, programs)
        return res
