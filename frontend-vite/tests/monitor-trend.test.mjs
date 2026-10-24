import { test } from 'node:test'
import assert from 'node:assert/strict'
import { trendInterval, trendPoints } from '../src/utils/monitorTrend.js'

test('each preset chooses readable time buckets rather than compressed raw points', () => {
  assert.deepEqual([1,6,24,168,720,2160,8760].map(hours => trendInterval(hours*3600)), [30,120,300,1800,7200,21600,86400])
})
test('weighted means preserve zero, extrema, counts and chronological order', () => {
  assert.deepEqual(trendPoints([[40,10,2,99,3],[10,0,0,0,1]],60), [[0,7.5,0,99,4]])
})
test('missing intervals break the curve instead of inventing data', () => {
  assert.deepEqual(trendPoints([[0,0],[180,10],[240,null]],60), [[0,0,0,0,1],[120000,null],[180000,10,10,10,1]])
  assert.deepEqual(trendPoints([],60), [])
})
