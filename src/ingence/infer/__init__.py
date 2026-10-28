"""Inference: classifying, grouping, proposing and flagging, with the statistical guards.

The package classifies, groups, proposes and flags. A candidate is drawn within a declared index,
cut at a bound and given a decision tier. No score is computed, and nothing decodes a candidate
into an assertion. A flag raises a finding, never a claim.

The tier is the decision. The score is a tiebreak within a tier only. No threshold in this
package may act as an accept or reject boundary.
"""
