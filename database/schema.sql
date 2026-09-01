-- MySQL dump 10.13  Distrib 8.0.42, for Win64 (x86_64)
--
-- Host: 127.0.0.1    Database: financial_database
-- ------------------------------------------------------
-- Server version	8.0.42

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `balance_sheet`
--

DROP TABLE IF EXISTS `balance_sheet`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `balance_sheet` (
  `serial_number` int NOT NULL AUTO_INCREMENT COMMENT '数据排序标识，用于区分不同记录',
  `stock_code` varchar(20) NOT NULL COMMENT '公司在证券市场的唯一标识代码',
  `stock_abbr` varchar(50) NOT NULL COMMENT '公司在证券市场的简称',
  `asset_cash_and_cash_equivalents` decimal(20,2) DEFAULT NULL COMMENT '公司持有的现金及可随时用于支付的存款',
  `asset_accounts_receivable` decimal(20,2) DEFAULT NULL COMMENT '公司因销售商品、提供劳务等应向购货单位或接受劳务单位收取的款项',
  `asset_inventory` decimal(20,2) DEFAULT NULL COMMENT '公司持有以备出售的产成品或商品、处在生产过程中的在产品、在生产过程或提供劳务过程中耗用的材料和物料等',
  `asset_trading_financial_assets` decimal(20,2) DEFAULT NULL COMMENT '公司为近期内出售而持有的金融资产，主要为银行理财产品',
  `asset_construction_in_progress` decimal(20,2) DEFAULT NULL COMMENT '公司正在建设尚未达到预定可使用状态的固定资产',
  `asset_total_assets` decimal(20,2) DEFAULT NULL COMMENT '公司报告期末全部资产的总额',
  `asset_total_assets_yoy_growth` decimal(10,4) DEFAULT NULL COMMENT '报告期末总资产较上年期末的变动比例',
  `liability_accounts_payable` decimal(20,2) DEFAULT NULL COMMENT '公司因购买材料、商品和接受劳务供应等经营活动应支付的款项',
  `liability_advance_from_customers` decimal(20,2) DEFAULT NULL COMMENT '公司按照合同规定向购货单位预收的款项，报告期内无余额，填列0',
  `liability_total_liabilities` decimal(20,2) DEFAULT NULL COMMENT '公司报告期末全部负债的总额',
  `liability_total_liabilities_yoy_growth` decimal(10,4) DEFAULT NULL COMMENT '报告期末总负债较上年期末的变动比例',
  `liability_contract_liabilities` decimal(20,2) DEFAULT NULL COMMENT '公司已收或应收客户对价而应向客户转让商品的义务',
  `liability_short_term_loans` decimal(20,2) DEFAULT NULL COMMENT '公司向银行或其他金融机构等借入的期限在1年以下（含1年）的各种借款',
  `asset_liability_ratio` decimal(10,4) DEFAULT NULL COMMENT '总负债/总资产×100%',
  `equity_unappropriated_profit` decimal(20,2) DEFAULT NULL COMMENT '公司累计未分配的利润（或累计未弥补的亏损）',
  `equity_total_equity` decimal(20,2) DEFAULT NULL COMMENT '公司报告期末所有者权益总额',
  `report_period` varchar(20) DEFAULT NULL COMMENT '数据对应的会计期间。其中，FY=年报（FullYear），Q1=一季度，HY=半年度，Q3=三季度。',
  `report_year` int DEFAULT NULL COMMENT '数据对应的年份',
  PRIMARY KEY (`serial_number`),
  UNIQUE KEY `stock_code` (`stock_code`,`report_period`,`report_year`),
  KEY `stock_code_2` (`stock_code`),
  KEY `report_year` (`report_year`)
) ENGINE=InnoDB AUTO_INCREMENT=1674 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='资产负债表';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `balance_sheet`
--


--
-- Table structure for table `cash_flow_sheet`
--

DROP TABLE IF EXISTS `cash_flow_sheet`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `cash_flow_sheet` (
  `serial_number` int NOT NULL AUTO_INCREMENT COMMENT '数据排序标识，用于区分不同记录',
  `stock_code` varchar(20) NOT NULL COMMENT '公司在证券市场的唯一标识代码',
  `stock_abbr` varchar(50) NOT NULL COMMENT '公司在证券市场的简称',
  `net_cash_flow` decimal(20,2) DEFAULT NULL COMMENT '即现金及现金等价物净增加额 (单位：元)',
  `net_cash_flow_yoy_growth` decimal(10,4) DEFAULT NULL COMMENT '本期净现金流较上年同期的变动比例 (%)',
  `operating_cf_net_amount` decimal(20,2) DEFAULT NULL COMMENT '公司日常经营活动产生的现金流入与流出的差额',
  `operating_cf_ratio_of_net_cf` decimal(10,4) DEFAULT NULL COMMENT '经营性现金流净额占净现金流总额的比例 (%)',
  `operating_cf_cash_from_sales` decimal(20,2) DEFAULT NULL COMMENT '公司通过销售商品、提供劳务实际收到的现金',
  `investing_cf_net_amount` decimal(20,2) DEFAULT NULL COMMENT '公司投资活动产生的现金流入与流出的差额',
  `investing_cf_ratio_of_net_cf` decimal(10,4) DEFAULT NULL COMMENT '投资性现金流净额占净现金流总额的比例 (%)',
  `investing_cf_cash_for_investments` decimal(20,2) DEFAULT NULL COMMENT '公司进行投资活动所支付的现金',
  `investing_cf_cash_from_investment_recovery` decimal(20,2) DEFAULT NULL COMMENT '公司收回前期投资所收到的现金',
  `financing_cf_cash_from_borrowing` decimal(20,2) DEFAULT NULL COMMENT '公司通过借款方式取得的现金',
  `financing_cf_cash_for_debt_repayment` decimal(20,2) DEFAULT NULL COMMENT '公司偿还前期借款所支付的现金',
  `financing_cf_net_amount` decimal(20,2) DEFAULT NULL COMMENT '公司融资活动产生的现金流入与流出的差额',
  `financing_cf_ratio_of_net_cf` decimal(10,4) DEFAULT NULL COMMENT '融资性现金流净额占净现金流总额的比例 (%)',
  `report_period` varchar(20) DEFAULT NULL COMMENT '数据对应的会计期间。其中，FY=年报（FullYear），Q1=一季度，HY=半年度，Q3=三季度。',
  `report_year` int DEFAULT NULL COMMENT '数据对应的年份',
  PRIMARY KEY (`serial_number`),
  UNIQUE KEY `stock_code` (`stock_code`,`report_period`,`report_year`),
  KEY `stock_code_2` (`stock_code`),
  KEY `report_year` (`report_year`)
) ENGINE=InnoDB AUTO_INCREMENT=1670 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票现金流量表数据表';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `cash_flow_sheet`
--


--
-- Table structure for table `core_performance_indicators_sheet`
--

DROP TABLE IF EXISTS `core_performance_indicators_sheet`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `core_performance_indicators_sheet` (
  `serial_number` int NOT NULL AUTO_INCREMENT COMMENT 'Primary Key',
  `stock_code` varchar(20) NOT NULL,
  `stock_abbr` varchar(50) NOT NULL,
  `eps` decimal(10,4) DEFAULT NULL,
  `total_operating_revenue` decimal(20,2) DEFAULT NULL,
  `operating_revenue_yoy_growth` decimal(10,4) DEFAULT NULL,
  `operating_revenue_qoq_growth` decimal(10,4) DEFAULT NULL,
  `net_profit_10k_yuan` decimal(20,2) DEFAULT NULL,
  `net_profit_yoy_growth` decimal(10,4) DEFAULT NULL,
  `net_profit_qoq_growth` decimal(10,4) DEFAULT NULL,
  `net_asset_per_share` decimal(10,4) DEFAULT NULL,
  `roe` decimal(10,4) DEFAULT NULL,
  `operating_cf_per_share` decimal(10,4) DEFAULT NULL,
  `net_profit_excl_non_recurring` decimal(20,2) DEFAULT NULL,
  `net_profit_excl_non_recurring_yoy` decimal(10,4) DEFAULT NULL,
  `gross_profit_margin` decimal(10,4) DEFAULT NULL,
  `net_profit_margin` decimal(10,4) DEFAULT NULL,
  `roe_weighted_excl_non_recurring` decimal(10,4) DEFAULT NULL,
  `report_period` varchar(20) DEFAULT NULL,
  `report_year` int DEFAULT NULL,
  PRIMARY KEY (`serial_number`),
  UNIQUE KEY `stock_code` (`stock_code`,`report_period`,`report_year`),
  KEY `stock_code_2` (`stock_code`),
  KEY `report_year` (`report_year`)
) ENGINE=InnoDB AUTO_INCREMENT=1680 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='核心业绩指标表';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `core_performance_indicators_sheet`
--


--
-- Table structure for table `dify`
--

DROP TABLE IF EXISTS `dify`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `dify` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT 'Primary Key',
  `create_time` datetime DEFAULT NULL COMMENT 'Create Time',
  `name` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `dify`
--


--
-- Table structure for table `dim_stock_info`
--

DROP TABLE IF EXISTS `dim_stock_info`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `dim_stock_info` (
  `stock_code` varchar(20) NOT NULL COMMENT '股票代码（主键）',
  `stock_abbr` varchar(50) NOT NULL COMMENT '股票简称',
  `stock_name` varchar(100) DEFAULT NULL COMMENT '股票全称',
  `list_date` date DEFAULT NULL COMMENT '上市日期',
  `delist_date` date DEFAULT NULL COMMENT '退市日期（NULL表示未退市）',
  `market` varchar(20) DEFAULT NULL COMMENT '所属市场（SH/SZ/BJ等）',
  `industry_code` varchar(20) DEFAULT NULL COMMENT '行业代码',
  `industry_name` varchar(50) DEFAULT NULL COMMENT '行业名称',
  `status` tinyint DEFAULT '1' COMMENT '状态（1=正常，0=退市）',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`stock_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票基础信息维度表';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `dim_stock_info`
--


--
-- Table structure for table `income_sheet`
--

DROP TABLE IF EXISTS `income_sheet`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `income_sheet` (
  `serial_number` int NOT NULL AUTO_INCREMENT COMMENT '数据排序标识，用于区分不同记录',
  `stock_code` varchar(20) NOT NULL COMMENT '公司在证券市场的唯一标识代码',
  `stock_abbr` varchar(50) NOT NULL COMMENT '公司在证券市场的简称',
  `net_profit` decimal(20,2) DEFAULT NULL COMMENT '公司报告期内实现的最终盈利或亏损总额',
  `net_profit_yoy_growth` decimal(10,4) DEFAULT NULL COMMENT '本期净利润较上年同期的变动比例 (%)',
  `total_profit` decimal(20,2) DEFAULT NULL COMMENT '公司报告期内实现的税前利润（亏损）总额',
  `operating_profit` decimal(20,2) DEFAULT NULL COMMENT '公司报告期内日常经营活动实现的利润（亏损）',
  `total_operating_revenue` decimal(20,2) DEFAULT NULL COMMENT '公司报告期内日常经营活动实现的全部收入总额',
  `operating_revenue_yoy_growth` decimal(10,4) DEFAULT NULL COMMENT '本期营业总收入较上年同期的变动比例 (%)',
  `other_income` decimal(20,2) DEFAULT NULL COMMENT '公司报告期内取得的与日常经营活动相关但不属于营业收入的政府补助等收益',
  `operating_expense_cost_of_sales` decimal(20,2) DEFAULT NULL COMMENT '即营业成本，公司销售商品、提供劳务等日常经营活动发生的成本',
  `operating_expense_selling_expenses` decimal(20,2) DEFAULT NULL COMMENT '公司为销售商品和材料、提供劳务过程中发生的各项费用',
  `operating_expense_administrative_expenses` decimal(20,2) DEFAULT NULL COMMENT '公司为组织和管理企业生产经营所发生的费用',
  `operating_expense_financial_expenses` decimal(20,2) DEFAULT NULL COMMENT '公司为筹集生产经营所需资金等而发生的筹资费用',
  `operating_expense_rnd_expenses` decimal(20,2) DEFAULT NULL COMMENT '公司进行研究与开发无形资产过程中发生的各项费用',
  `operating_expense_taxes_and_surcharges` decimal(20,2) DEFAULT NULL COMMENT '公司经营活动应负担的相关税费',
  `total_operating_expenses` decimal(20,2) DEFAULT NULL COMMENT '公司报告期内发生的全部经营支出总额',
  `asset_impairment_loss` decimal(20,2) DEFAULT NULL COMMENT '公司计提各项资产减值准备所形成的损失',
  `credit_impairment_loss` decimal(20,2) DEFAULT NULL COMMENT '公司计提各项金融工具信用减值准备所形成的损失',
  `report_period` varchar(20) DEFAULT NULL COMMENT '数据对应的会计期间。其中，FY=年报（FullYear），Q1=一季度，HY=半年度，Q3=三季度。',
  `report_year` int DEFAULT NULL COMMENT '数据对应的年份',
  PRIMARY KEY (`serial_number`),
  UNIQUE KEY `stock_code_2` (`stock_code`,`report_period`,`report_year`),
  KEY `stock_code` (`stock_code`),
  KEY `report_year` (`report_year`)
) ENGINE=InnoDB AUTO_INCREMENT=1676 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票利润表明细数据表（含费用结构）';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `income_sheet`
--


--
-- Table structure for table `log_data_validation`
--

DROP TABLE IF EXISTS `log_data_validation`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `log_data_validation` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `validation_batch_id` varchar(50) NOT NULL COMMENT '校验批次ID',
  `table_name` varchar(50) NOT NULL COMMENT '被校验表名',
  `validation_type` varchar(50) NOT NULL COMMENT '校验类型',
  `validation_rule` varchar(200) DEFAULT NULL COMMENT '校验规则',
  `total_records` int DEFAULT NULL COMMENT '总记录数',
  `passed_records` int DEFAULT NULL COMMENT '通过记录数',
  `failed_records` int DEFAULT NULL COMMENT '失败记录数',
  `error_details` text COMMENT '错误详情',
  `validation_status` tinyint DEFAULT NULL COMMENT '状态（1=通过，0=失败）',
  `validated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `validated_by` varchar(50) DEFAULT NULL COMMENT '校验执行人/系统',
  PRIMARY KEY (`id`),
  KEY `idx_batch` (`validation_batch_id`),
  KEY `idx_table` (`table_name`),
  KEY `idx_status` (`validation_status`)
) ENGINE=InnoDB AUTO_INCREMENT=2143 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='数据校验日志表';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `log_data_validation`
--


--
-- Dumping routines for database 'financial_database'
--
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-04-24 16:50:07
