# Мониторинг production стэкинга

## Когда проверять
- Первая проверка: после 50 settled predictions (~1-2 недели)
- Полная проверка: после 150 settled predictions (~3-4 недели)
- Регулярная: раз в неделю после накопления данных

## Команда для проверки

```bash
docker compose exec scheduler python scripts/production_monitor.py
docker compose exec scheduler python scripts/production_monitor.py --min-settled 50 --output results/production_report.json
```

## Что ожидаем (из backtest)
- RPS ~ 0.196 (+/-0.01 допустимо)
- Stacking > DC-only > Poisson baseline
- Calibration error < 0.05
- CLV > 0 (модель бьёт рынок)

## Решения после проверки

### Если всё хорошо (RPS < 0.200, calibration < 0.03, CLV > 0):
1. Активировать Kelly: `ENABLE_KELLY=true`, `KELLY_FRACTION=0.25`
2. Рассмотреть Dirichlet (marginal, но может чуть помочь)
3. Перейти к H2H / standings / advanced features

### Если RPS нормальный, но калибровка плохая (> 0.05):
1. Обучить Dirichlet на production predictions
2. НЕ включать Kelly
3. Диагностика: какие bins miscalibrated (домашние? ничьи? фавориты?)

### Если RPS хуже baseline (> 0.210):
1. Откатить на DC-only: `USE_STACKING=false`
2. Проверить: не изменились ли odds sources / data quality
3. Перегенерировать training data на свежих данных
4. Диагностика через `scripts/ablation_study.py` на последних матчах

### Если CLV отрицательный:
1. Модель может быть хорошо откалибрована, но не иметь edge над рынком
2. Рассмотреть: поднять VALUE_THRESHOLD, сузить ODDS_RANGE
3. Добавить Pinnacle closing line как feature / calibration target

## Пороги для решений

| Метрика | Порог | Действие |
|---------|-------|----------|
| Calibration error < 0.03 | Good | Можно Kelly |
| Calibration error < 0.05 | OK | Quarter-Kelly с осторожностью |
| Calibration error > 0.05 | Bad | Не включать Kelly, обучить Dirichlet |
| Production RPS < 0.200 | Expected | Модель работает как backtest |
| Production RPS 0.200-0.210 | Acceptable | Нормальная variance |
| Production RPS > 0.210 | Worse | Проблема, нужна диагностика |
| CLV > 0 | Positive | Модель бьёт рынок |
| Stacking RPS < DC-only RPS | Expected | Стэкинг работает |
