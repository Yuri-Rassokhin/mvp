#!/usr/bin/bash

mvp call temperature-prediction-madrid train '{"dataset": "/home/ubuntu/mvp/components/xgboost/madrid_weather.csv", "target":"target_temp_mean"}'
