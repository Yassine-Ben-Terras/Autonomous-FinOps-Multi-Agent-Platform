# CloudSense OPA Policies — Rego v1
package cloudsense
import future.keywords.if
import future.keywords.in

default allow := false
destructive_actions := {"stop", "terminate", "delete", "detach"}
production_envs := {"production", "prod", "live"}

allow if { input.action_type in {"investigate", "report", "tag", "purchase-commitment"} }
allow if { input.approved == true; input.approved_by != ""; count(input.approved_by) > 0 }

deny contains msg if {
    input.action_type in destructive_actions
    input.environment in production_envs
    not input.approved
    msg := sprintf("%s on %s requires explicit approval", [input.action_type, input.environment])
}
deny contains msg if { input.action_type == "delete"; msg := "Delete actions permanently blocked" }
deny contains msg if {
    input.action_type == "right-size"; input.risk_level == "high"; not input.approved
    msg := "High-risk right-sizing requires human approval"
}
deny contains msg if {
    input.projected_monthly_savings < 50; input.action_type in destructive_actions
    msg := "Destructive action denied: projected savings below $50/month threshold"
}
allow if { count(deny) == 0; input.action_type != "" }
