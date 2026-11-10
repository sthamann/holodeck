"""Public observation vocabulary shared by the teacher driver and learner."""
from itertools import product

FIELDS = ('country', 'group', 'amount', 'weight')
COUNTRIES = ('DE', 'AT')
GROUPS = ('retail', 'trade')
AMOUNTS = (3000, 6000, 9000)
WEIGHTS = (1000, 3000, 5000)
CONTEXTS = tuple(product(COUNTRIES, GROUPS, AMOUNTS, WEIGHTS))
HOLDOUT_CONTEXTS = tuple(product(COUNTRIES, GROUPS, (5999, 8999, 10000), (2999, 4999, 6000)))
