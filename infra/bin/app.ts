#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { L1StorageStack }   from '../lib/l1-storage-stack';
import { L2VectorStack }    from '../lib/l2-vector-stack';
import { L3GraphStack }     from '../lib/l3-graph-stack';
import { L4CatalogStack }   from '../lib/l4-catalog-stack';
import { L5OntologyStack }  from '../lib/l5-ontology-stack';
import { L6ReasoningStack } from '../lib/l6-reasoning-stack';
import { L7AgentStack }     from '../lib/l7-agent-stack';

const app = new cdk.App();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region:  process.env.CDK_DEFAULT_REGION ?? 'us-east-1',
};

const l1 = new L1StorageStack(app,  'VbcL1StorageStack',   { env });
const l2 = new L2VectorStack(app,   'VbcL2VectorStack',    { env });
const l3 = new L3GraphStack(app,    'VbcL3GraphStack',     { env });
const l4 = new L4CatalogStack(app,  'VbcL4CatalogStack',   { env });
const l5 = new L5OntologyStack(app, 'VbcL5OntologyStack',  { env });
const l6 = new L6ReasoningStack(app,'VbcL6ReasoningStack', { env });
const l7 = new L7AgentStack(app,    'VbcL7AgentStack',     { env });
