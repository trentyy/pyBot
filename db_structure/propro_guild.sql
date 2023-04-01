-- phpMyAdmin SQL Dump
-- version 4.9.7
-- https://www.phpmyadmin.net/
--
-- Host: localhost
-- Generation Time: May 04, 2021 at 11:10 AM
-- Server version: 8.0.18
-- PHP Version: 7.3.11

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
SET AUTOCOMMIT = 0;
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- Database: `propro_guild`
--

-- --------------------------------------------------------

--
-- Table structure for table `tweets`
--

CREATE TABLE `tweets` (
  `isForwarded` tinyint(1) NOT NULL DEFAULT '0',
  `id` bigint(20) UNSIGNED NOT NULL,
  `username` varchar(15) COLLATE utf8mb4_general_ci NOT NULL,
  `author_id` bigint(20) UNSIGNED NOT NULL,
  `created_at` datetime NOT NULL,
  `text` varchar(280) COLLATE utf8mb4_general_ci NOT NULL,
  `yt_videoid` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `videos`
--

CREATE TABLE `videos` (
  `isForwarded` tinyint(4) NOT NULL DEFAULT '0',
  `videoId` tinytext COLLATE utf8mb4_general_ci NOT NULL,
  `channel` tinytext COLLATE utf8mb4_general_ci NOT NULL,
  `scheduledStartTime` datetime DEFAULT NULL,
  `actualStartTime` datetime DEFAULT NULL,
  `actualEndTime` datetime DEFAULT NULL,
  `title` varchar(100) COLLATE utf8mb4_general_ci NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Indexes for dumped tables
--

--
-- Indexes for table `tweets`
--
ALTER TABLE `tweets`
  ADD PRIMARY KEY (`id`),
  ADD KEY `author_id` (`author_id`),
  ADD KEY `yt_videoid` (`yt_videoid`),
  ADD KEY `created_at` (`created_at`),
  ADD KEY `isForwarded` (`isForwarded`);

--
-- Indexes for table `videos`
--
ALTER TABLE `videos`
  ADD PRIMARY KEY (`videoId`(11)),
  ADD KEY `scheduledStartTime` (`scheduledStartTime`),
  ADD KEY `channel` (`channel`(24)),
  ADD KEY `actualEndTime` (`actualEndTime`),
  ADD KEY `actualStartTime` (`actualStartTime`),
  ADD KEY `isForwarded` (`isForwarded`);
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
