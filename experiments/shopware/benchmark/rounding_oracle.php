<?php declare(strict_types=1);

require '/var/www/html/vendor/autoload.php';

use Shopware\Core\Checkout\Cart\Price\CashRounding;
use Shopware\Core\Checkout\Cart\Price\GrossPriceCalculator;
use Shopware\Core\Checkout\Cart\Price\Struct\QuantityPriceDefinition;
use Shopware\Core\Checkout\Cart\Tax\Struct\TaxRule;
use Shopware\Core\Checkout\Cart\Tax\Struct\TaxRuleCollection;
use Shopware\Core\Checkout\Cart\Tax\TaxCalculator;
use Shopware\Core\Framework\DataAbstractionLayer\Pricing\CashRoundingConfig;

$tax = new TaxCalculator();
$round = new CashRounding();
$calculator = new GrossPriceCalculator($tax, $round);
$rounding = new CashRoundingConfig(2, 0.01, true);
$cases = json_decode(file_get_contents('php://stdin'), true, flags: JSON_THROW_ON_ERROR);
foreach ($cases as [$priceCents, $rate, $quantity]) {
    $value = ['unit' => $priceCents, 'rate' => $rate, 'quantity' => $quantity];
    foreach ([14, 17] as $precision) {
        ini_set('precision', (string) $precision);
        $rules = new TaxRuleCollection([new TaxRule($rate, 100)]);
        $definition = new QuantityPriceDefinition($priceCents / 100, $rules, $quantity);
        $definition->setIsCalculated(true);
        $price = $calculator->calculate($definition, $rounding);
        $unit = $round->cashRound($priceCents / 100, $rounding);
        $unitTax = $tax->calculateGrossTaxes($unit, $rules)->first()->getTax();
        $value[(string) $precision] = [
            'unit_tax' => sprintf('%.17g', $unitTax),
            'raw_line_tax' => sprintf('%.17g', $unitTax * $quantity),
            'tax_cents' => (int) round($price->getCalculatedTaxes()->first()->getTax() * 100)
        ];
    }
    echo json_encode($value, JSON_THROW_ON_ERROR), PHP_EOL;
}
