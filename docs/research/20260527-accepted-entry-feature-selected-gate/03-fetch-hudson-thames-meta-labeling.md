# Meta Labeling (A Toy Example) - Hudson & Thames

Loading [MathJax]/extensions/tex2jax.js

[![Image 1: Hudson & Thames](https://hudsonthames.org/wp-content/uploads/2021/01/logo-horisontal-white-teal-1-1030x418.png)](https://hudsonthames.org/)

*   [HOME](https://hudsonthames.org/)
*   [PYTHON LIBRARIES](https://hudsonthames.org/meta-labeling-a-toy-example#)
    *   [ARBITRAGELAB](https://hudsonthames.org/arbitragelab/)
    *   [MLFINLAB](https://hudsonthames.org/mlfinlab/)
    *   [PORTFOLIOLAB](https://hudsonthames.org/portfoliolab/)

*   [COURSES](https://hudsonthames.org/meta-labeling-a-toy-example#)
    *   [MASTERING BACKTESTING](https://www.udemy.com/course/mastering-backtesting-for-algorithmic-trading/?referralCode=DED2C1825744E0151EAA)

*   [FELLOWSHIP PROGRAM](https://hudsonthames.org/fellowship-program/)
*   [BLOG](https://hudsonthames.org/research/)
    *   [RESEARCH ARTICLES](https://hudsonthames.org/research/)
    *   [COMPREHENSIVE INTRODUCTION TO PAIRS TRADING](https://hudsonthames.org/definitive-guide-to-pairs-trading/)
    *   [MODERN GUIDE TO PORTFOLIO OPTIMIZATION](https://hudsonthames.org/modern-guide-to-portfolio-optimization/)

*   [CONTACT](https://hudsonthames.org/about-us/)
*   [PORTAL LOGIN](https://portal.hudsonthames.org/)
*   [Search](https://hudsonthames.org/meta-labeling-a-toy-example?s=)
*   [**Menu**Menu](https://hudsonthames.org/meta-labeling-a-toy-example#)

# Meta Labeling (A Toy Example)

Join the [Reading Group and Community](https://hudsonthames.org/reading-group/): Stay up to date with the latest developments in Financial Machine Learning!

[JOIN NOW](https://hudsonthames.org/reading-group/)

This blog post investigates the idea of Meta Labeling and tries to help build an intuition for what is taking place. The idea of meta labeling is first mentioned in the textbook [Advances in Financial Machine Learning](https://www.amazon.co.uk/Advances-Financial-Machine-Learning-Marcos/dp/1119482089) by Marcos Lopez de Prado and promises to improve model and strategy performance metrics by helping to filter-out false positives.

In this blog post we make use of a computer vision problem known as the MNIST handwritten digit classification. By making use of a non financial time series data set we can illustrate the components that make up meta labeling more clearly. Lets begin!

The following section is taken directly from the textbook and left in its original form as to make sure no errors are introduced by means of interpretation.

_Advances in Financial Machine Learning, Chapter 3, page 50. Reads:_

Suppose that you have a model for setting the side of the bet (long or short). You just need to learn the size of that bet, which includes the possibility of no bet at all (zero size). This is a situation that practitioners face regularly. We often know whether we want to buy or sell a product, and the only remaining question is how much money we should risk in such a bet. We do not want the ML algorithm to learn the side, just to tell us what is the appropriate size. At this point, it probably does not surprise you to hear that no book or paper has so far discussed this common problem. Thankfully, that misery ends here.

I call this problem meta labeling because we want to build a secondary ML model that learns how to use a primary exogenous model.

The ML algorithm will be trained to decide whether to take the bet or pass, a purely binary prediction. When the predicted label is 1, we can use the probability of this secondary prediction to derive the size of the bet, where the side (sign) of the position has been set by the primary model.

## How to use Meta Labeling

Binary classification problems present a trade-off between type-I errors (false positives) and type-II errors (false negatives). In general, increasing the true positive rate of a binary classifier will tend to increase its false positive rate. The receiver operating characteristic (ROC) curve of a binary classifier measures the cost of increasing the true positive rate, in terms of accepting higher false positive rates.

![Image 2: Precision vs. Recall](https://hudsonthames.org/wp-content/uploads/2021/11/Precisionrecall-566x1030.png)

Figure 1: Precision and Recall

[Wikipedia, the free encyclopedia 2019](https://en.wikipedia.org/wiki/Precision_and_recall)

The image illustrates the so-called “confusion matrix.” On a set of observations, there are items that exhibit a condition (positives, left rectangle), and items that do not exhibit a condition (negative, right rectangle). A binary classifier predicts that some items exhibit the condition (ellipse), where the TP area contains the true positives and the TN area contains the true negatives. This leads to two kinds of errors: false positives (FP) and false negatives (FN). “Precision” is the ratio between the TP area and the area in the ellipse. “Recall” is the ratio between the TP area and the area in the left rectangle. This notion of recall (aka true positive rate) is in the context of classification problems, the analogous to “power” in the context of hypothesis testing. “Accuracy” is the sum of the TP and TN areas divided by the overall set of items (square). In general, decreasing the FP area comes at a cost of increasing the FN area, because higher precision typically means fewer calls, hence lower recall. Still, there is some combination of precision and recall that maximizes the overall efficiency of the classifier. The F1-score measures the efficiency of a classifier as the harmonic average between precision and recall.

Meta labeling is particularly helpful when you want to achieve higher F1-scores. First, we build a model that achieves high recall, even if the precision is not particularly high. Second, we correct for the low precision by applying meta labeling to the positives predicted by the primary model.

Meta labeling will increase your F1-score by filtering out the false positives, where the majority of positives have already been identified by the primary model. Stated differently, the role of the secondary ML algorithm is to determine whether a positive from the primary (exogenous) model is true or false. It is not its purpose to come up with a betting opportunity. Its purpose is to determine whether we should act or pass on the opportunity that has been presented.

## Additional uses of Meta Labeling

Meta labeling is a very powerful tool to have in your arsenal, for four additional reasons. First, ML algorithms are often criticized as black boxes.

Meta labeling allows you to build an ML system on top of a white box (like a fundamental model founded on economic theory). This ability to transform a fundamental model into an ML model should make meta labeling particularly useful to “quantamental” firms. Second, the effects of overfitting are limited when you apply meta labeling, because ML will not decide the side of your bet, only the size. Third, by decoupling the side prediction from the size prediction, meta labeling enables sophisticated strategy structures. For instance, consider that the features driving a rally may differ from the features driving a sell-off. In that case, you may want to develop an ML strategy exclusively for long positions, based on the buy recommendations of a primary model, and an ML strategy exclusively for short positions, based on the sell recommendations of an entirely different primary model. Fourth, achieving high accuracy on small bets and low accuracy on large bets will ruin you. As important as identifying good opportunities is to size them properly, so it makes sense to develop an ML algorithm solely focused on getting that critical decision (sizing) right. In my experience, meta labeling ML models can deliver more robust and reliable outcomes than standard labeling models.

## Toy Example

To illustrate the concept we made use of the MNIST data set to train a binary classifier on identifying the number 3, from a set that only includes the digits 3 and 5. The reason for this is that the number 3 looks very similar to 5 and we expect there to be some overlap in the data, i.e. the data are not linearly separable. Another reason we chose the MNIST dataset to illustrate the concept, is that MNIST is a solved problem and we can witness improvements in performance metrics with ease.

![Image 3](https://hudsonthames.org/wp-content/uploads/2019/04/mnist.png)

Figure 2: Handwritten 5 and 3

### Model Architecture

The following image explains the model architecture. The first step is to train a primary model (binary classification) with a high recall. Second a threshold level is determined at which the primary model has a high recall, ROC curves could be used to help determine a good level. Third the features from the first model are concatenated with the predictions from the first model, into a new feature set for the secondary model. Meta labels are used as the target variable in the second model. Now fit the second model. Fourth the prediction from the secondary model is combined with the prediction from the primary model and only where both are true, is your final prediction true. I.e. if your primary model predicts a 3 and your secondary model says you have a high probability of the primary model being correct, is your final prediction a 3, else not 3.

![Image 4](https://hudsonthames.org/wp-content/uploads/2021/11/meta_labeling.png)

Figure 3: Meta Label Model Architecture

### Build Primary Model with High Recall

The first step is to train a primary model (binary classification). For this we trained a logistic regression, using the keras package. The data are split into a 90\% train, 10\% validation. This allows us to see when we are over-fitting.

Second a threshold level is determined at which the primary model has a high recall, ROC curves could be used to help determine a good level. A high recall means that the primary model captures the majority of positive samples even if there are a large number of false positives. The meta model will correct this by reducing the number of false positives and thus boosting all performance metrics.

![Image 5](https://hudsonthames.org/wp-content/uploads/2019/04/roc.png)

Figure 4: Receiver Operating Characteristic (ROC) Curve

### Build Meta Model

Third the features from the first model are concatenated with the predictions from the first model, into a new feature set for the secondary model. Meta labels are used as the target variable in the second model. Now fit the second model.

Meta labels are defined as: If the primary model’s predictions matches the actual values, then we label it as 1, else 0. In this example we said that if an observation was a true positive or true negative then label it as 1(i.e. the model is correct), else 0 (the model in incorrect). Note that because it is categorical, we have to add One Hot Encoding.

### Evaluate Performance

Fourth the prediction from the secondary model is combined with the prediction from the primary model and only where both are true, is your final prediction true. e.g. if your primary model predicts a 3 and your secondary model says you have a high probability of the primary model being correct, is your final prediction a 3, else not a 3.

The section below shows the performance of the primary model vs the performance of using Meta labeling, on out-of-sample data. Notice how the performance metrics improve.

![Image 6: Performance metrics of a meta labeling model.](https://hudsonthames.org/wp-content/uploads/2019/04/meta_metrics.png)

Figure 5: Meta Labeling Performance Metrics

We can see that in the confusion matrix, that the false positives from the primary model, are now being correctly identified as true negatives with the help of meta labeling. This leads to a boost in performance metrics. Meta labeling works as advertised!

To read more about meta labeling used in a trading strategy (Trend following and Mean reverting), be sure to checkout our article titled: [Does Meta Labeling Add to Signal Efficay?](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy/)

![Image 7: Article: Does Meta Labeling add to Signal Efficacy?](https://hudsonthames.org/wp-content/uploads/2019/07/report1.png)

[![Image 8](https://hudsonthames.org/wp-content/uploads/2019/08/quantocracy.png)](https://quantocracy.com/)

Popular

Recent

Comments

Tags

Popular

*   [![Image 9](https://hudsonthames.org/wp-content/uploads/2021/01/Into-to-Copula-Pairs-Trading-36x36.png)**Copula for Pairs Trading: A Detailed, But Practical Int...January 20, 2021 - 1:21 pm**](https://hudsonthames.org/copula-for-pairs-trading-introduction/ "Copula for Pairs Trading: A Detailed, But Practical Introduction")
*   [![Image 10: Best research practices for your quant research group](https://hudsonthames.org/wp-content/uploads/2022/01/Best_research_practices_for_your_quant_research_group-36x36.jpg)**Best Research Practices for Your Quantitative Finance Research...January 14, 2022 - 1:26 pm**](https://hudsonthames.org/best-research-practices-for-your-quantitative-finance-research-group/ "Best Research Practices for Your Quantitative Finance Research Group")
*   [![Image 11](https://hudsonthames.org/wp-content/uploads/2023/04/john-towner-p-rN-n6Miag-unsplash-scaled.jpg)**Machine Learning Trading Essentials (Part 1): Financial...April 13, 2023 - 10:47 am**](https://hudsonthames.org/machine-learning-trading-essentials-part-1-financial-data-structures/ "Machine Learning Trading Essentials (Part 1): Financial Data Structures")
*   [![Image 12](https://hudsonthames.org/wp-content/uploads/2021/01/Copy-of-Copula-Strategy-Variations-in-the-Mispricing-Index-Trading7-36x36.png)**Employing Machine Learning for Pairs Selection January 25, 2021 - 3:09 pm**](https://hudsonthames.org/employing-machine-learning-for-trading-pairs-selection/ "Employing Machine Learning for Pairs Selection")
*   [![Image 13](https://hudsonthames.org/wp-content/uploads/2021/02/Copy-of-Copula-Strategy-Variations-in-the-Mispricing-Index-Trading-36x36.png)**Copula for Pairs Trading: Sampling and Fitting to Data February 4, 2021 - 2:45 pm**](https://hudsonthames.org/copula-for-pairs-trading-sampling-and-fitting/ "Copula for Pairs Trading: Sampling and Fitting to Data")
*   [![Image 14: C-Vine Copula Strategy Thumbnail](https://hudsonthames.org/wp-content/uploads/2021/05/Copy-of-Introduction-to-Vine-Copula-for-Statistical-Arbitrage2-36x36.png)**Copula for Statistical Arbitrage: A C-Vine Copula Trading...May 10, 2021 - 7:09 pm**](https://hudsonthames.org/copula-for-statistical-arbitrage-a-c-vine-copula-trading-strategy/ "Copula for Statistical Arbitrage: A C-Vine Copula Trading Strategy")
*   [![Image 15: Stocks Selection Methods](https://hudsonthames.org/wp-content/uploads/2021/04/Copy-of-Introduction-to-Vine-Copula-for-Statistical-Arbitrage1-36x36.png)**Copula for Statistical Arbitrage: Stocks Selection Meth...April 28, 2021 - 12:11 pm**](https://hudsonthames.org/copula-for-statistical-arbitrage-stocks-selection-methods/ "Copula for Statistical Arbitrage: Stocks Selection Methods")
*   [![Image 16](https://hudsonthames.org/wp-content/uploads/2021/04/Intro-to-Vine-Copula-36x36.png)**Copula for Statistical Arbitrage: A Practical Intro to Vine...April 14, 2021 - 2:54 pm**](https://hudsonthames.org/a-practical-intro-to-vine-copula/ "Copula for Statistical Arbitrage: A Practical Intro to Vine Copula")
*   [![Image 17](https://hudsonthames.org/wp-content/uploads/2020/10/pmfg_graph_only-36x36.png)**Exploring the PMFG Portfolios for Covid-19 Robustness October 4, 2020 - 10:43 pm**](https://hudsonthames.org/exploring-the-pmfg-portfolios-for-covid-19-robustness/ "Exploring the PMFG Portfolios for Covid-19 Robustness")
*   [![Image 18](https://hudsonthames.org/wp-content/uploads/2020/09/Pairs-Trading-with-Ornstein-Uhlenbeck-Model-1-36x36.png)**Optimal Stopping in Pairs Trading: Ornstein-Uhlenbeck M...September 21, 2020 - 8:59 pm**](https://hudsonthames.org/optimal-stopping-in-pairs-trading-ornstein-uhlenbeck-model/ "Optimal Stopping in Pairs Trading: Ornstein-Uhlenbeck Model")

Recent

*   [![Image 19](https://hudsonthames.org/wp-content/uploads/2023/10/Featured-image-wordpress-blog-2023-10-10-36x36.png)**Dynamically combining mean reversion and momentum investment...October 10, 2023 - 2:41 pm**](https://hudsonthames.org/dynamically-combining-mean-reversion-and-momentum-investment-strategies/ "Dynamically combining mean reversion and momentum investment strategies")
*   [![Image 20](https://hudsonthames.org/wp-content/uploads/2023/09/DOCKER-X-MLFinlab-5-36x36.png)**Docker + MLFinlab now in Open Beta to All Subscribers September 13, 2023 - 1:56 pm**](https://hudsonthames.org/docker-mlfinlab-now-in-open-beta-to-all-subscribers/ "Docker + MLFinlab now in Open Beta to All Subscribers")
*   [![Image 21](https://hudsonthames.org/wp-content/uploads/2023/04/LB_HudsonThames_ProductLogos_MLFinLab-03-36x36.png)**Release Announcement: MLFinlab v2.2.0 September 6, 2023 - 12:28 pm**](https://hudsonthames.org/release-announcement-mlfinlab-v2-2-0/ "Release Announcement: MLFinlab v2.2.0")
*   [![Image 22](https://hudsonthames.org/wp-content/uploads/2023/06/LB_Hudson_Thames_ProductLogos_PortfolioLab-03-1-36x36.png)**Release Announcement: PortoflioLab v0.6.0 September 6, 2023 - 8:55 am**](https://hudsonthames.org/release-announcement-portofliolab-v0-6-0/ "Release Announcement: PortoflioLab v0.6.0")
*   [![Image 23](https://hudsonthames.org/wp-content/uploads/2023/04/LB_HudsonThames_ProductLogos_MLFinLab-03-36x36.png)**Release Announcement: MLFinlab v2.1.0 August 17, 2023 - 8:29 am**](https://hudsonthames.org/release-announcement-mlfinlab-v2-1-0/ "Release Announcement: MLFinlab v2.1.0")
*   [![Image 24](https://hudsonthames.org/wp-content/uploads/2023/06/LB_Hudson_Thames_ProductLogos_PortfolioLab-03-1-36x36.png)**Release announcement: PortfolioLab v0.5.0 July 13, 2023 - 1:15 pm**](https://hudsonthames.org/release-announcement-portfoliolab-v0-5-0/ "Release announcement: PortfolioLab v0.5.0")
*   [![Image 25](https://hudsonthames.org/wp-content/uploads/2023/07/docker_beta-36x36.png)**Docker + MLFinlab closed beta announcement July 5, 2023 - 8:01 pm**](https://hudsonthames.org/docker-mlfinlab-closed-beta-announcement/ "Docker + MLFinlab closed beta announcement")
*   [![Image 26](https://hudsonthames.org/wp-content/uploads/2023/06/LB_Hudson_Thames_ProductLogos_ArbitrageLab-03-1-1-36x36.png)**Celebrating ArbitrageLab v0.8 — 85% lifetime discount June 30, 2023 - 12:47 pm**](https://hudsonthames.org/celebrating-arbitragelab-v0-8-85-lifetime-discount/ "Celebrating ArbitrageLab v0.8 — 85% lifetime discount")
*   [![Image 27](https://hudsonthames.org/wp-content/uploads/2023/06/LB_Hudson_Thames_ProductLogos_ArbitrageLab-03-1-36x36.png)**Release announcement: ArbitrageLab v0.8 June 28, 2023 - 1:29 pm**](https://hudsonthames.org/release-announcement-arbitragelab-v0-8/ "Release announcement: ArbitrageLab v0.8")
*   [![Image 28](https://hudsonthames.org/wp-content/uploads/2023/06/manhatten-scaled.jpg)**Breaking Down the “cold-start” Problem in Quantitative...June 13, 2023 - 1:25 pm**](https://hudsonthames.org/breaking-down-the-cold-start-problem-in-quantitative-finance/ "Breaking Down the “cold-start” Problem in Quantitative Finance")

Comments

*   [**[…] Taking your MLFinLab strategy live [Hudson and...July 1, 2023 - 5:45 am by Quantocracy's Daily Wrap for 06/30/2023 - Quantocracy**](https://hudsonthames.org/?p=10932/#comment-48 "Taking your MLFinLab strategy live")
*   [**[…] Machine Learning Trading Essentials (Part 2):...April 28, 2023 - 5:30 am by Quantocracy's Daily Wrap for 04/27/2023 - Quantocracy**](https://hudsonthames.org/machine-learning-trading-essentials-part-2-fractionally-differentiated-features-filtering-and-labelling/#comment-46 "Machine Learning Trading Essentials (Part 2): Fractionally differentiated features, Filtering, and Labelling")
*   [**[…] enjoying our journey towards building a successful...April 26, 2023 - 9:01 am by Machine Learning Trading Essentials (Part 2): Fractionally differentiated features, Filtering, and Labelling - Hudson & Thames**](https://hudsonthames.org/machine-learning-trading-essentials-part-1-financial-data-structures/#comment-45 "Machine Learning Trading Essentials (Part 1): Financial Data Structures")
*   [**[…] Machine Learning Trading Essentials (Part 1):...April 16, 2023 - 5:30 am by Quantocracy's Daily Wrap for 04/15/2023 - Quantocracy**](https://hudsonthames.org/machine-learning-trading-essentials-part-1-financial-data-structures/#comment-41 "Machine Learning Trading Essentials (Part 1): Financial Data Structures")
*   [**[…] QuantConnect Integration with MlFinLab [Hudson...October 22, 2022 - 5:30 am by Quantocracy's Daily Wrap for 10/21/2022 - Quantocracy**](https://hudsonthames.org/quantconnect-integration-with-mlfinlab/#comment-40 "QuantConnect Integration with MlFinLab")
*   [**[…] Best Research Practices for Your Quant Group [Hudson...January 28, 2022 - 6:45 am by Quantocracy's Daily Wrap for 01/19/2022 - Quantocracy**](https://hudsonthames.org/best-research-practices-for-your-quantitative-finance-research-group/#comment-39 "Best Research Practices for Your Quantitative Finance Research Group")
*   [**[…] on our last article regarding best practices for...January 25, 2022 - 6:48 am by How to Build a World-Class Quant Team - Hudson & Thames**](https://hudsonthames.org/best-research-practices-for-your-quantitative-finance-research-group/#comment-38 "Best Research Practices for Your Quantitative Finance Research Group")
*   [**[…] on our last article regarding best practices for...January 23, 2022 - 11:42 pm by How to Build the Best Quant Team in the World - Hudson & Thames**](https://hudsonthames.org/best-research-practices-for-your-quantitative-finance-research-group/#comment-37 "Best Research Practices for Your Quantitative Finance Research Group")
*   [**[…] Pairs Trading with Stochastic Control and OU process...July 12, 2021 - 1:45 am by Quantocracy's Daily Wrap for 06/22/2021 | Quantocracy**](https://hudsonthames.org/pairs-trading-with-stochastic-control-and-ou-process/#comment-35 "Pairs Trading with Stochastic Control and OU process")
*   [**[…] Copula for Statistical Arbitrage: C-Vine Copula...May 19, 2021 - 5:45 am by Quantocracy's Daily Wrap for 05/10/2021 | Quantocracy**](https://hudsonthames.org/copula-for-statistical-arbitrage-a-c-vine-copula-trading-strategy/#comment-34 "Copula for Statistical Arbitrage: A C-Vine Copula Trading Strategy")

Tags

[Asset Management](https://hudsonthames.org/tag/asset-management/)[Benchmarks](https://hudsonthames.org/tag/benchmarks/)[Code Tutorial](https://hudsonthames.org/tag/code-tutorial/)[cointegration](https://hudsonthames.org/tag/cointegration/)[copula](https://hudsonthames.org/tag/copula/)[Correlation Estimation](https://hudsonthames.org/tag/correlation-estimation/)[Covariance Matrix](https://hudsonthames.org/tag/covariance-matrix/)[data](https://hudsonthames.org/tag/data/)[Distance Approach](https://hudsonthames.org/tag/distance-approach/)[Distance measures](https://hudsonthames.org/tag/distance-measures/)[Ensemble](https://hudsonthames.org/tag/ensemble/)[ESG](https://hudsonthames.org/tag/esg/)[futures contracts](https://hudsonthames.org/tag/futures-contracts/)[Github](https://hudsonthames.org/tag/github/)[Hierarchical Clustering](https://hudsonthames.org/tag/hierarchical-clustering/)[Interpretability](https://hudsonthames.org/tag/interpretability/)[lessons](https://hudsonthames.org/tag/lessons/)[Machine Learning](https://hudsonthames.org/tag/machine-learning/)[Mean Reversion](https://hudsonthames.org/tag/mean-reversion/)[meta-labeling](https://hudsonthames.org/tag/meta-labeling/)[Minimum Spanning Tree](https://hudsonthames.org/tag/minimum-spanning-tree/)[mixed copula](https://hudsonthames.org/tag/mixed-copula/)[Momentum](https://hudsonthames.org/tag/momentum/)[Networks](https://hudsonthames.org/tag/networks/)[OLS](https://hudsonthames.org/tag/ols/)[open-source](https://hudsonthames.org/tag/open-source/)[Optimal Stopping](https://hudsonthames.org/tag/optimal-stopping/)[Ornstein-Uhlenbeck](https://hudsonthames.org/tag/ornstein-uhlenbeck/)[OU Model](https://hudsonthames.org/tag/ou-model/)[Pairs Selection](https://hudsonthames.org/tag/pairs-selection/)[Pairs Trading](https://hudsonthames.org/tag/pairs-trading/)[Pattern Matching](https://hudsonthames.org/tag/pattern-matching/)[PCA](https://hudsonthames.org/tag/pca/)[Planar Maximally Filtered Graph](https://hudsonthames.org/tag/planar-maximally-filtered-graph/)[Portfolio Optimisation](https://hudsonthames.org/tag/portfolio-optimisation/)[Portfolio Selection](https://hudsonthames.org/tag/portfolio-selection/)[python](https://hudsonthames.org/tag/python/)[Random walks](https://hudsonthames.org/tag/random-walks/)[research](https://hudsonthames.org/tag/research/)[Risk estimation](https://hudsonthames.org/tag/risk-estimation/)[Risk Parity](https://hudsonthames.org/tag/risk-parity/)[Sustainable Investing](https://hudsonthames.org/tag/sustainable-investing/)[Synthetic Data](https://hudsonthames.org/tag/synthetic-data/)[Trading](https://hudsonthames.org/tag/trading/)[Trading Strategy](https://hudsonthames.org/tag/trading-strategy/)

© Copyright - Hudson & Thames | [Privacy Policy](https://hudsonthames.org/wp-content/uploads/2021/06/PrivacyPolicy.pdf) | [GDPR Policy](https://hudsonthames.org/wp-content/uploads/2021/06/GDPR-Policy.pdf)

[MLFinLab on PyPi Index![Image 29](https://hudsonthames.org/wp-content/uploads/2019/07/pypi-80x80.jpg)](https://hudsonthames.org/mlfinlab-on-pypi-index/)[![Image 30](https://hudsonthames.org/wp-content/uploads/2019/08/meta_labeling-80x80.png)Does Meta Labeling Add to Signal Efficacy?](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy-triple-barrier-method/)

[Scroll to top](https://hudsonthames.org/meta-labeling-a-toy-example#top "Scroll to top")
