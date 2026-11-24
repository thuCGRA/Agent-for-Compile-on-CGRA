/*
 * Constraints.h
 *
 * M1：把"agent 想怎么放"变成 mapper 的硬约束。
 *
 * 只做两件可验证的事：
 *   pins      某些节点必须落在给定 (x,y)
 *   forbidden 某些节点不许落在给定 (x,y)
 *
 * 为什么只有 strict 没有 prefer：候选集后面进的是 priority_queue，
 * 顺序注入不生效；而 dpPenaltyMap 在 PathFinderMapper.cpp 里只写不读（死代码），
 * 加"软提示"会变成假装有效。宁可少一个模式，不留假功能。
 *
 * 为什么允许多个节点 pin 到同一个 PE：CGRA 是时分复用的，同一 PE 在不同相位
 * 承载不同节点是常态。实测 array_add 的基线解里节点 0 与 12 都在 PE(1,2)。
 * 第一版把这条当错误，结果把 mapper 自己产出的合法解判成非法 —— 与当初
 * 把 OSTORE 判成算术算子同一类错误：把我以为的约束当成了硬约束。
 * 同一 PE 上放不放得下，交给 mapper 的 checkDPFree 判，这里不越权。
 *
 * 语义与 ~/cac 的 LLMMapper 一致，但这里不碰相位：相位由 mapper 自己推。
 */

#ifndef CONSTRAINTS_H_
#define CONSTRAINTS_H_

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include "morpher/arch/json.hpp"

namespace CGRAXMLCompile
{

class Constraints
{
public:
	struct Pin
	{
		int x;
		int y;
	};

	bool enabled = false;
	// 同一节点既 pin 又 forbidden 时，forbidden 优先（宁可保守地报失败，
	// 也不要在矛盾约束下产出一个"看起来成功"的映射）。
	std::map<int, Pin> pins;
	std::map<int, std::vector<Pin> > forbidden;

	bool hasPin(int idx) const
	{
		return pins.find(idx) != pins.end();
	}

	bool isForbidden(int idx, int x, int y) const
	{
		std::map<int, std::vector<Pin> >::const_iterator it = forbidden.find(idx);
		if (it == forbidden.end())
			return false;
		for (size_t i = 0; i < it->second.size(); ++i)
		{
			if (it->second[i].x == x && it->second[i].y == y)
				return true;
		}
		return false;
	}

	// 核心判据：节点 idx 能否落在 (x,y)
	bool allowed(int idx, int x, int y) const
	{
		if (!enabled)
			return true;
		if (isForbidden(idx, x, y))
			return false;
		if (hasPin(idx))
		{
			const Pin &p = pins.find(idx)->second;
			return p.x == x && p.y == y;
		}
		// 没被 pin 的节点不受额外限制：它落在哪个 PE 是 mapper 的自由，
		// 即使那个 PE 上已经有被 pin 的节点（不同相位可以共存）。
		return true;
	}

	bool loadFromFile(const std::string &path)
	{
		std::ifstream in(path.c_str());
		if (!in.good())
		{
			std::cerr << "constraints: cannot open " << path << "\n";
			return false;
		}
		nlohmann::json j;
		try
		{
			in >> j;
		}
		catch (std::exception &e)
		{
			std::cerr << "constraints: " << path << " is not valid JSON: " << e.what() << "\n";
			return false;
		}

		if (j.contains("pin_mode"))
		{
			std::string mode = j["pin_mode"].get<std::string>();
			if (mode != "strict")
			{
				std::cerr << "constraints: pin_mode='" << mode
						  << "' is not supported (only 'strict')\n";
				return false;
			}
		}

		if (j.contains("pins"))
		{
			for (auto it = j["pins"].begin(); it != j["pins"].end(); ++it)
			{
				int idx = std::atoi(it.key().c_str());
				Pin p;
				p.x = it.value()[0].get<int>();
				p.y = it.value()[1].get<int>();
				// 不检查"两个节点 pin 到同一 PE"：时分复用下这是合法的，
				// 是否真放得下由 mapper 的 checkDPFree 在搜索时判。
				pins[idx] = p;
			}
		}

		if (j.contains("forbidden"))
		{
			for (auto it = j["forbidden"].begin(); it != j["forbidden"].end(); ++it)
			{
				int idx = std::atoi(it.key().c_str());
				std::vector<Pin> v;
				for (size_t i = 0; i < it.value().size(); ++i)
				{
					Pin p;
					p.x = it.value()[i][0].get<int>();
					p.y = it.value()[i][1].get<int>();
					v.push_back(p);
				}
				forbidden[idx] = v;
			}
		}

		// 自相矛盾要在开工前就报，而不是让 mapper 跑一遍才失败
		for (auto it = pins.begin(); it != pins.end(); ++it)
		{
			if (isForbidden(it->first, it->second.x, it->second.y))
			{
				std::cerr << "constraints: node " << it->first << " is both pinned to and forbidden at ("
						  << it->second.x << "," << it->second.y << ")\n";
				return false;
			}
		}

		enabled = true;
		return true;
	}

	std::string describe() const
	{
		std::ostringstream os;
		if (!enabled)
		{
			os << "constraints: none";
			return os.str();
		}
		os << "constraints: " << pins.size() << " pin(s)";
		for (auto it = pins.begin(); it != pins.end(); ++it)
			os << " [" << it->first << "->(" << it->second.x << "," << it->second.y << ")]";
		size_t nf = 0;
		for (auto it = forbidden.begin(); it != forbidden.end(); ++it)
			nf += it->second.size();
		os << ", " << nf << " forbidden";
		return os.str();
	}
};

} // namespace CGRAXMLCompile

#endif /* CONSTRAINTS_H_ */
