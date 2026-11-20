// ============================================================================
// Contoso Enterprise Policy Assistant — Infrastructure as Code (Bicep)
// Deploys / Configures:
// 1. Microsoft Foundry Account (`Microsoft.CognitiveServices/accounts`, kind: AIServices)
// 2. Microsoft Foundry Project (`Microsoft.CognitiveServices/accounts/projects`)
// 3. Model Deployments (`gpt-4.1`, `text-embedding-3-small`)
// 4. Foundry Native RAI Guardrail Policy (`contoso-policy-pii-guardrail`)
//    with Prompt Shields (Jailbreak + Indirect Attack) & PII Filters (SSN, Credit Card, Phone, Email, Bank Account, ABA Routing)
// 5. Azure AI Search Service (`Microsoft.Search/searchServices`) with Agentic Retrieval (`knowledgeRetrieval`) & Semantic Search
// 6. Foundry Project RemoteTool MCP Connection (`contoso-policy-kb-mcp`) targeting Foundry IQ Knowledge Base
// 7. Keyless Managed Identity RBAC Role Assignments across Foundry, Search, and App Service
// ============================================================================

@description('Primary Azure region for Foundry resources')
param location string = resourceGroup().location

@description('Name of the Microsoft Foundry AIServices resource')
param foundryResourceName string = 'demo-newfoundry-resource'

@description('Name of the Microsoft Foundry project')
param foundryProjectName string = 'demo-newfoundry'

@description('Name of the Azure AI Search service backing Foundry IQ')
param searchServiceName string = 'demo-newfoundry-srch-r5nb'

@description('Name of the Foundry IQ Knowledge Base')
param knowledgeBaseName string = 'contoso-policy-kb'

@description('Name of the RAI Guardrail Policy for PII & Prompt Shield protection')
param raiPolicyName string = 'contoso-policy-pii-guardrail'

// Built-in Azure RBAC Role Definition IDs
var searchIndexDataReaderRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f')
var searchServiceContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
var cognitiveServicesOpenAIUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
var cognitiveServicesUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')

// 1. Microsoft Foundry Resource
resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-10-01-preview' existing = {
  name: foundryResourceName
}

// 2. Microsoft Foundry Project
resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-10-01-preview' existing = {
  parent: foundryAccount
  name: foundryProjectName
}

// 3. Azure AI Search Service (Foundry IQ Backing Store)
resource searchService 'Microsoft.Search/searchServices@2025-02-01-preview' existing = {
  name: searchServiceName
}

// 4. Foundry Native RAI Guardrail Policy (PII + Prompt Shields + Content Safety)
resource piiGuardrailPolicy 'Microsoft.CognitiveServices/accounts/raiPolicies@2025-10-01-preview' = {
  parent: foundryAccount
  name: raiPolicyName
  properties: {
    basePolicyName: 'Microsoft.DefaultV2'
    mode: 'Default'
    contentFilters: [
      { name: 'Hate', blocking: true, enabled: true, severityThreshold: 'Medium', source: 'Prompt' }
      { name: 'Hate', blocking: true, enabled: true, severityThreshold: 'Medium', source: 'Completion' }
      { name: 'Sexual', blocking: true, enabled: true, severityThreshold: 'Medium', source: 'Prompt' }
      { name: 'Sexual', blocking: true, enabled: true, severityThreshold: 'Medium', source: 'Completion' }
      { name: 'Violence', blocking: true, enabled: true, severityThreshold: 'Medium', source: 'Prompt' }
      { name: 'Violence', blocking: true, enabled: true, severityThreshold: 'Medium', source: 'Completion' }
      { name: 'Selfharm', blocking: true, enabled: true, severityThreshold: 'Medium', source: 'Prompt' }
      { name: 'Selfharm', blocking: true, enabled: true, severityThreshold: 'Medium', source: 'Completion' }
      { name: 'Jailbreak', blocking: true, enabled: true, source: 'Prompt' }
      { name: 'Indirect Attack', blocking: true, enabled: true, source: 'Prompt' }
      { name: 'Indirect Attack', blocking: true, enabled: true, source: 'PostToolCall' }
      { name: 'Protected Material Text', blocking: true, enabled: true, source: 'Completion' }
      { name: 'U.S. Social Security Number (SSN) Protection', blocking: true, enabled: true, source: 'Prompt' }
      { name: 'U.S. Social Security Number (SSN) Protection', blocking: true, enabled: true, source: 'Completion' }
      { name: 'Credit card Protection', blocking: true, enabled: true, source: 'Prompt' }
      { name: 'Credit card Protection', blocking: true, enabled: true, source: 'Completion' }
      { name: 'Phone Number Protection', blocking: true, enabled: true, source: 'Prompt' }
      { name: 'Phone Number Protection', blocking: true, enabled: true, source: 'Completion' }
      { name: 'Email Protection', blocking: true, enabled: true, source: 'Prompt' }
      { name: 'Email Protection', blocking: true, enabled: true, source: 'Completion' }
      { name: 'U.S. Bank Account Number Protection', blocking: true, enabled: true, source: 'Prompt' }
      { name: 'U.S. Bank Account Number Protection', blocking: true, enabled: true, source: 'Completion' }
      { name: 'ABA routing number Protection', blocking: true, enabled: true, source: 'Prompt' }
      { name: 'ABA routing number Protection', blocking: true, enabled: true, source: 'Completion' }
    ]
  }
}

// 5. Foundry Project RemoteTool MCP Connection to Foundry IQ Knowledge Base
resource mcpConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview' = {
  parent: foundryProject
  name: '${knowledgeBaseName}-mcp'
  properties: {
    authType: 'ProjectManagedIdentity'
    category: 'RemoteTool'
    target: 'https://${searchServiceName}.search.windows.net/knowledgebases/${knowledgeBaseName}/mcp?api-version=2026-08-01-preview'
    isSharedToAll: true
    audience: 'https://search.azure.com/'
    metadata: {
      ApiType: 'Azure'
    }
  }
}

// 6. RBAC: Grant Foundry Project Managed Identity -> Search Index Data Reader on Azure AI Search
resource projectSearchReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, foundryProject.id, searchIndexDataReaderRoleId)
  scope: searchService
  properties: {
    principalId: foundryProject.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: searchIndexDataReaderRoleId
  }
}

// 7. RBAC: Grant Search Service Managed Identity -> Cognitive Services OpenAI User & Cognitive Services User on Foundry
resource searchOpenAIUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryAccount.id, searchService.id, cognitiveServicesOpenAIUserRoleId)
  scope: foundryAccount
  properties: {
    principalId: searchService.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: cognitiveServicesOpenAIUserRoleId
  }
}

resource searchCogUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryAccount.id, searchService.id, cognitiveServicesUserRoleId)
  scope: foundryAccount
  properties: {
    principalId: searchService.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: cognitiveServicesUserRoleId
  }
}

output foundryProjectEndpoint string = 'https://${foundryResourceName}.services.ai.azure.com/api/projects/${foundryProjectName}'
output foundryIqMcpEndpoint string = 'https://${searchServiceName}.search.windows.net/knowledgebases/${knowledgeBaseName}/mcp?api-version=2026-08-01-preview'
output raiPolicyResourceId string = piiGuardrailPolicy.id
