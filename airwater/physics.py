"""Shared physical/economic assumptions (documented; not experimentally calibrated).

Used by both selector.py (ranking, cycle-count and regen-temperature
feasibility) and decision.py (energy/cost estimate) so the two stay
numerically consistent -- a material that selector.py says can't fit enough
cycles into the energy budget should show the same shortfall in decision.py's
energy_budget check, not a second, differently-parameterized estimate.
"""

SORBENT_SPECIFIC_HEAT_KJ_PER_KGK = 1.2  # typical porous solid/composite heat capacity
WATER_DESORPTION_ENTHALPY_KJ_PER_KG = 2450.0  # vaporization enthalpy plus MOF binding-energy premium
COLLECTOR_AREA_M2_PER_KG = 0.35  # assumed solar-thermal collector footprint scaling with sorbent mass
SOLAR_COLLECTION_EFFICIENCY = 0.45  # flat-plate/PV-thermal hybrid collector efficiency
COLLECTOR_HEAT_LOSS_W_M2K = 6.0  # glazed flat-plate approx; higher = more heat loss, lower achievable delta-T
ENERGY_COST_PER_KWH = {"Solar only": 0.03, "Waste heat": 0.01, "Electricity or hybrid": 0.15}
MATERIAL_COST_FACTOR_PER_KG_CYCLE = 0.08  # amortized sorbent wear/replacement cost at cost_score=0
PRACTICAL_MAX_TEMP_C = {"Waste heat": 120.0, "Electricity or hybrid": 180.0}  # not flux-limited, so treated as reachable up to a practical ceiling
REGEN_SAFETY_MARGIN_C = 5.0  # required margin below the achievable/user-limit temperature to count as feasible
